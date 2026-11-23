"""Durable, account-scoped Workers AI request reservations.

Amounts are thousandths of a neuron. Callers round reservations and observed
usage up. A request with an unknown outcome retains its full reservation.
This budget covers only traffic admitted through the same database.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import sqlite3


NORMAL_LIMIT = 7_500_000
HARD_LIMIT = 10_000_000


class BudgetRefused(RuntimeError):
    """Sending the request would violate the configured budget."""


class WorkersAIBudget:
    def __init__(self, database: Path, account_id: str, *, carry_across_days=False):
        if not re.fullmatch(r'[0-9a-f]{32}', account_id):
            raise ValueError('Expected a Cloudflare account ID')
        self.database = database
        self.account_id = account_id
        self.carry_across_days = carry_across_days

    @contextmanager
    def _transaction(self):
        fd = os.open(self.database, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        db = sqlite3.connect(self.database, timeout=30)
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                db.execute(
                    'CREATE TABLE IF NOT EXISTS requests ('
                    'account TEXT NOT NULL, day TEXT NOT NULL, id TEXT NOT NULL, '
                    'amount INTEGER NOT NULL, settled INTEGER NOT NULL, '
                    'PRIMARY KEY (account, id))')
                db.execute(
                    'CREATE TABLE IF NOT EXISTS cross_day_usage ('
                    'account TEXT NOT NULL, id TEXT NOT NULL, day TEXT NOT NULL, '
                    'amount INTEGER NOT NULL, settled INTEGER NOT NULL, '
                    'PRIMARY KEY (account, id))')
                yield db
        finally:
            db.close()

    def reserve(self, request_id: str, maximum: int, *, new_task: bool,
                now: datetime | None = None) -> None:
        """Persist a one-use request reservation before dispatch."""
        with self._transaction() as db:
            self._reserve(db, request_id, maximum, new_task, _utc_day(now))

    def _headroom(self, db, day, limit):
        used, exceeded = db.execute(
            'SELECT COALESCE(SUM(amount),0), COALESCE(MAX(settled),0) '
            'FROM requests WHERE account=? AND (day=? OR (? AND settled=0))',
            (self.account_id, day, self.carry_across_days)
        ).fetchone()
        if self.carry_across_days:
            carried, carry_exceeded = db.execute(
                'SELECT COALESCE(SUM(amount),0), COALESCE(MAX(settled),0) '
                'FROM cross_day_usage WHERE account=? AND day=?', (self.account_id, day)
            ).fetchone()
            used += carried
            exceeded = max(exceeded, carry_exceeded)
        return 0 if exceeded == 2 else max(0, limit - used)

    def _reserve(self, db, request_id, maximum, new_task, day):
        if not request_id or type(maximum) is not int or not 0 < maximum <= HARD_LIMIT:
            raise ValueError('Expected a request ID and a bounded positive reservation')
        if db.execute('SELECT 1 FROM requests WHERE account=? AND id=?',
                      (self.account_id, request_id)).fetchone():
            raise BudgetRefused('Request already reserved; reconcile before replay')
        limit = NORMAL_LIMIT if new_task else HARD_LIMIT
        if maximum > self._headroom(db, day, limit):
            raise BudgetRefused('Request exceeds the daily Workers AI budget')
        db.execute('INSERT INTO requests VALUES (?, ?, ?, ?, 0)',
                   (self.account_id, day, request_id, maximum))

    def settle(self, request_id: str, actual: int, *, gateway_receipt: str | None = None,
               now: datetime | None = None) -> None:
        """Record observed usage against the original reservation day."""
        if type(actual) is not int or actual < 0:
            raise ValueError('Expected nonnegative observed usage')
        with self._transaction() as db:
            updated = db.execute(
                'UPDATE requests SET settled=CASE WHEN ?>amount THEN 2 ELSE 1 END, amount=? '
                'WHERE account=? AND id=? AND settled=0',
                (actual, actual, self.account_id, request_id))
            if updated.rowcount != 1:
                raise BudgetRefused('No outstanding reservation for this request')
            if self.carry_across_days and actual:
                day, settled = db.execute(
                    'SELECT day,settled FROM requests WHERE account=? AND id=?',
                    (self.account_id, request_id)).fetchone()
                completion_day = _utc_day(now)
                if day != completion_day:
                    db.execute('INSERT INTO cross_day_usage VALUES (?, ?, ?, ?, ?)',
                               (self.account_id, request_id, completion_day, actual, settled))
            if gateway_receipt is not None:
                db.execute(
                    'CREATE TABLE IF NOT EXISTS gateway_receipts ('
                    'account TEXT NOT NULL, request_id TEXT NOT NULL, receipt TEXT NOT NULL, '
                    'PRIMARY KEY (account, request_id))')
                db.execute('INSERT INTO gateway_receipts VALUES (?, ?, ?)',
                           (self.account_id, request_id, gateway_receipt))


PROJECTS = frozenset({'UIF', 'OffMenu', 'ANQR', 'CIX', 'CQ'})
PAID_ACCOUNT = 'Paid'


def _utc_day(now=None):
    now = now if now is not None else datetime.now(timezone.utc)
    if now.utcoffset() is None:
        raise ValueError('Expected a timezone-aware timestamp')
    return now.astimezone(timezone.utc).date().isoformat()


class ProjectAccountRouter:
    """Bind attempts to accounts before dispatch using the current project state."""

    def __init__(self, database: Path, accounts: dict[str, str], *, eligible_projects=None):
        if (accounts.keys() not in (PROJECTS, PROJECTS | {PAID_ACCOUNT})
                or len(set(accounts.values())) != len(accounts)):
            raise ValueError('Expected one distinct Cloudflare account per project')
        self.eligible_projects = PROJECTS if eligible_projects is None else frozenset(eligible_projects)
        if not self.eligible_projects <= accounts.keys():
            raise ValueError('Unknown eligible Cloudflare project')
        self.accounts = {
            project: WorkersAIBudget(database, account_id, carry_across_days=project == PAID_ACCOUNT)
            for project, account_id in accounts.items()
        }

    def has_started(self, attempt_id: str) -> bool:
        """Check whether the first request was durably reserved for this attempt."""
        budget = next(iter(self.accounts.values()))
        with budget._transaction() as db:
            if not db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='assignments'"
            ).fetchone():
                return False
            return db.execute('SELECT 1 FROM assignments WHERE attempt=?',
                              (attempt_id,)).fetchone() is not None

    def independent_reviewer(self, project: str, author: str, active_projects: set[str],
                             maximum: int) -> str | None:
        """Choose an eligible distinct account for a settled, same-project author."""
        with self.accounts[project]._transaction() as db:
            if not db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='routed_requests'"
            ).fetchone():
                return None
            row = db.execute(
                'SELECT a.account,count(q.id),count(CASE WHEN r.settled=1 THEN 1 END) '
                'FROM assignments a JOIN routed_requests q ON a.attempt=q.attempt '
                'LEFT JOIN requests r ON q.id=r.id AND a.account=r.account '
                'WHERE a.attempt=? AND a.project=? GROUP BY a.account',
                (author, project)).fetchone()
            if row is None or not row[1] or row[1] != row[2]:
                return None
            day = _utc_day()
            choices = [
                name for name in sorted(self.eligible_projects)
                if self.accounts[name].account_id != row[0]
                and (name == project or name not in active_projects)
                and self.accounts[name]._headroom(db, day, NORMAL_LIMIT) >= maximum
            ]
            return min(choices, key=lambda name: (name == PAID_ACCOUNT, name != project, name),
                       default=None)

    def reserve_request(self, project: str, attempt_id: str, request_id: str,
                        maximum: int, active_projects: set[str], *,
                        request_sha256: str,
                        review_of: str | None = None, reviewer_project: str | None = None,
                        now: datetime | None = None) -> WorkersAIBudget:
        """Bind an attempt and reserve its request in the same transaction."""
        if not active_projects <= PROJECTS or project not in active_projects or not attempt_id:
            raise BudgetRefused('Expected an active project and an attempt ID')
        if not request_id or not re.fullmatch(r'[0-9a-f]{64}', request_sha256):
            raise ValueError('Expected request identity and SHA-256')
        if type(maximum) is not int or not 0 < maximum <= HARD_LIMIT:
            raise ValueError('Expected a bounded positive reservation')
        if (review_of is None) != (reviewer_project is None):
            raise BudgetRefused('Review requires a named reviewer account and author attempt')
        if reviewer_project is not None and reviewer_project not in self.accounts:
            raise BudgetRefused('Reviewer account is not configured')
        preferred = self.accounts[project]
        candidates = [preferred, *(self.accounts[owner] for owner in sorted(PROJECTS - active_projects))]
        with preferred._transaction() as db:
            day = _utc_day(now)
            db.execute(
                'CREATE TABLE IF NOT EXISTS assignments ('
                'attempt TEXT PRIMARY KEY, project TEXT NOT NULL, '
                'account TEXT NOT NULL, day TEXT NOT NULL)')
            db.execute(
                'CREATE TABLE IF NOT EXISTS independent_reviews ('
                'attempt TEXT PRIMARY KEY, author_attempt TEXT NOT NULL)')
            db.execute(
                'CREATE TABLE IF NOT EXISTS routed_requests ('
                'id TEXT PRIMARY KEY, attempt TEXT NOT NULL, request_sha256 TEXT NOT NULL)')
            if db.execute('SELECT 1 FROM routed_requests WHERE id=?', (request_id,)).fetchone():
                raise BudgetRefused('Request already routed; reconcile before replay')
            recorded_review = db.execute(
                'SELECT author_attempt FROM independent_reviews WHERE attempt=?', (attempt_id,)
            ).fetchone()
            existing = db.execute(
                'SELECT project, account FROM assignments WHERE attempt=?', (attempt_id,)
            ).fetchone()
            if existing:
                owner, account = existing
                if (owner != project or recorded_review != ((review_of,) if review_of is not None else None)
                        or reviewer_project is not None
                        and account != self.accounts[reviewer_project].account_id):
                    raise BudgetRefused('Attempt identity changed')
                for name, budget in self.accounts.items():
                    if name in self.eligible_projects and budget.account_id == account:
                        budget._reserve(db, request_id, maximum, False, day)
                        db.execute('INSERT INTO routed_requests VALUES (?, ?, ?)',
                                   (request_id, attempt_id, request_sha256))
                        return budget
                raise BudgetRefused('Attempt account is no longer configured')

            if reviewer_project is not None:
                author = db.execute(
                    'SELECT project, account FROM assignments WHERE attempt=?', (review_of,)
                ).fetchone()
                if not author or author[0] != project:
                    raise BudgetRefused('Review requires a bound author attempt on the same project')
                reviewer = self.accounts[reviewer_project]
                if reviewer.account_id == author[1]:
                    raise BudgetRefused('Author and reviewer must use different accounts')
                candidates = [reviewer]

            eligible = {self.accounts[name] for name in self.eligible_projects}
            candidates = [budget for budget in candidates if budget in eligible]
            headroom = {budget: budget._headroom(db, day, NORMAL_LIMIT) for budget in candidates}
            candidates = [budget for budget in candidates if headroom[budget] >= maximum]
            paid = self.accounts.get(PAID_ACCOUNT)
            if (reviewer_project is None and paid in eligible
                    and (active_projects != PROJECTS or not candidates)):
                headroom[paid] = paid._headroom(db, day, NORMAL_LIMIT)
                if headroom[paid] >= maximum:
                    candidates.append(paid)
            if not candidates:
                raise BudgetRefused('No eligible account has sufficient daily budget')
            selected = max(candidates, key=lambda budget: headroom[budget])
            selected._reserve(db, request_id, maximum, True, day)
            db.execute('INSERT INTO assignments VALUES (?, ?, ?, ?)',
                       (attempt_id, project, selected.account_id, day))
            db.execute('INSERT INTO routed_requests VALUES (?, ?, ?)',
                       (request_id, attempt_id, request_sha256))
            if review_of is not None:
                db.execute('INSERT INTO independent_reviews VALUES (?, ?)', (attempt_id, review_of))
            return selected
