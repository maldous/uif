"""Durable, account-scoped Workers AI request reservations.

Amounts are thousandths of a neuron. Callers round reservations and observed
usage up. A request with an unknown outcome retains its full reservation.
This budget covers only traffic admitted through the same database.
"""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import sqlite3
from types import MappingProxyType

from .account_admission import FreeRoute, allows_request


NORMAL_LIMIT = 7_500_000
HARD_LIMIT = 10_000_000
RECOVERY_FRESH_SECONDS = 90


@dataclass(frozen=True)
class AccountHold:
    account_id: str
    request_id: str
    reason: str
    observed_at: datetime


@dataclass(frozen=True)
class AccountRecovery:
    """Evidence returned by an independently authenticated account reader."""

    hold: AccountHold
    observed_at: datetime
    evidence_sha256: str


class BudgetRefused(RuntimeError):
    """Sending the request would violate the configured budget."""


class WorkersAIBudget:
    def __init__(self, database: Path, account_id: str):
        if not re.fullmatch(r'[0-9a-f]{32}', account_id):
            raise ValueError('Expected a Cloudflare account ID')
        self.database = database
        self.account_id = account_id

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
                db.execute(
                    'CREATE TABLE IF NOT EXISTS account_holds ('
                    'account TEXT NOT NULL, request_id TEXT NOT NULL, reason TEXT NOT NULL, '
                    'observed_at TEXT NOT NULL, recovery_sha256 TEXT, '
                    'PRIMARY KEY (account, request_id))')
                yield db
        finally:
            db.close()

    def reserve(self, request_id: str, maximum: int, *, new_task: bool,
                now: datetime | None = None) -> None:
        """Persist a one-use reservation. First calls and continuations share the cap."""
        with self._transaction() as db:
            self._reserve(db, request_id, maximum, _utc_day(now))

    def _headroom(self, db, day):
        if db.execute(
                'SELECT 1 FROM account_holds WHERE account=? AND recovery_sha256 IS NULL LIMIT 1',
                (self.account_id,)).fetchone():
            return 0
        used, exceeded = db.execute(
            'SELECT COALESCE(SUM(amount),0), COALESCE(MAX(settled),0) '
            'FROM requests WHERE account=? AND (day=? OR settled=0)',
            (self.account_id, day),
        ).fetchone()
        carried, carry_exceeded = db.execute(
            'SELECT COALESCE(SUM(amount),0), COALESCE(MAX(settled),0) '
            'FROM cross_day_usage WHERE account=? AND day=?', (self.account_id, day),
        ).fetchone()
        if max(exceeded, carry_exceeded) == 2:
            return 0
        return max(0, NORMAL_LIMIT - 1 - used - carried)

    def _reserve(self, db, request_id, maximum, day):
        if not request_id or type(maximum) is not int or not 0 < maximum <= HARD_LIMIT:
            raise ValueError('Expected a request ID and a bounded positive reservation')
        if db.execute('SELECT 1 FROM requests WHERE account=? AND id=?',
                      (self.account_id, request_id)).fetchone():
            raise BudgetRefused('Request already reserved; reconcile before replay')
        if maximum > self._headroom(db, day):
            raise BudgetRefused('Request exceeds the daily Workers AI budget')
        db.execute('INSERT INTO requests VALUES (?, ?, ?, ?, 0)',
                   (self.account_id, day, request_id, maximum))

    def hold(self, request_id, reason, *, now=None):
        """Hold this account without settling or replaying the original request."""
        if reason not in {'rate-limit', 'authentication', 'provider-unavailable'}:
            raise ValueError('Unknown Workers AI account hold reason')
        now = _utc_time(now)
        with self._transaction() as db:
            if not db.execute(
                    'SELECT 1 FROM requests WHERE account=? AND id=? AND settled=0',
                    (self.account_id, request_id)).fetchone():
                raise BudgetRefused('Account hold requires an outstanding original request')
            db.execute('INSERT OR IGNORE INTO account_holds VALUES (?, ?, ?, ?, NULL)',
                       (self.account_id, request_id, reason, now.isoformat()))

    def recover(self, read_recovery, *, now=None):
        """Reconcile individually held operations through a trusted read-only adapter.

        This restores eligibility only. Outstanding neuron reservations still count
        until the original usage is independently settled.
        """
        with self._transaction() as db:
            pending = db.execute(
                'SELECT request_id, reason, observed_at FROM account_holds '
                'WHERE account=? AND recovery_sha256 IS NULL ORDER BY observed_at, request_id',
                (self.account_id,)).fetchall()

        for request_id, reason, timestamp in pending:
            hold = AccountHold(self.account_id, request_id, reason, datetime.fromisoformat(timestamp))
            try:
                receipt = read_recovery(hold)
            except (OSError, ValueError):
                # This observation failed. Other pending holds still get a read.
                continue
            if not isinstance(receipt, AccountRecovery) or receipt.hold != hold:
                continue
            if not isinstance(receipt.evidence_sha256, str) or not re.fullmatch(
                    r'[0-9a-f]{64}', receipt.evidence_sha256):
                continue
            if not isinstance(receipt.observed_at, datetime) or receipt.observed_at.utcoffset() is None:
                continue
            with self._transaction() as db:
                current = _utc_time(now)
                if (receipt.observed_at <= hold.observed_at
                        or current.date() != _utc_time(receipt.observed_at).date()
                        or not 0 <= (current - receipt.observed_at).total_seconds() <= RECOVERY_FRESH_SECONDS):
                    continue
                db.execute(
                    'UPDATE account_holds SET recovery_sha256=? WHERE account=? AND request_id=? '
                    'AND reason=? AND observed_at=? AND recovery_sha256 IS NULL',
                    (receipt.evidence_sha256, self.account_id, request_id, reason, timestamp))

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
            if actual:
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


def _utc_time(now=None):
    now = now if now is not None else datetime.now(timezone.utc)
    if now.utcoffset() is None:
        raise ValueError('Expected a timezone-aware timestamp')
    return now.astimezone(timezone.utc)


def _utc_day(now=None):
    return _utc_time(now).date().isoformat()


class ProjectAccountRouter:
    """Bind attempts to accounts before dispatch using the current project state."""

    def __init__(self, database: Path, accounts: dict[str, str], *, eligible_projects=None, strict_routes=None):
        if (accounts.keys() not in (PROJECTS, PROJECTS | {PAID_ACCOUNT})
                or len(set(accounts.values())) != len(accounts)):
            raise ValueError('Expected one distinct Cloudflare account per project')
        self.eligible_projects = PROJECTS if eligible_projects is None else frozenset(eligible_projects)
        if not self.eligible_projects <= accounts.keys():
            raise ValueError('Unknown eligible Cloudflare project')
        self.strict_routes = None
        if strict_routes is not None:
            routes = dict(strict_routes)
            if (not routes or not routes.keys() <= PROJECTS
                    or not routes.keys() <= self.eligible_projects
                    or PAID_ACCOUNT in self.eligible_projects
                    or any(type(route) is not FreeRoute or route.account_id != accounts[name]
                           for name, route in routes.items())):
                raise ValueError('Strict account admission requires bound Free project routes')
            self.strict_routes = MappingProxyType(routes)
        self.accounts = {
            project: WorkersAIBudget(database, account_id)
            for project, account_id in accounts.items()
        }

    def refresh_holds(self, read_recovery):
        """Consume qualifying evidence from the existing owner's trusted reader."""
        for name in sorted(self.eligible_projects):
            self.accounts[name].recover(read_recovery)

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

    def _invalid_strict_maximum(self, maximum):
        return self.strict_routes is not None and (
            type(maximum) is not int or not 0 < maximum <= HARD_LIMIT)

    def _qualified_headroom(self, db, name, day, maximum, snapshots, request_not_after, now):
        available = self.accounts[name]._headroom(db, day)
        if available < maximum or self.strict_routes is None:
            return available
        route = self.strict_routes.get(name)
        if route is None or not allows_request(
                route, snapshots.get(name), now=now, request_not_after=request_not_after,
                outstanding_local=NORMAL_LIMIT - 1 - available, request_maximum=maximum):
            return 0
        return available

    def independent_reviewer(self, project: str, author: str, active_projects: set[str],
                             maximum: int, *, usage_snapshots=None, request_not_after=None) -> str | None:
        """Choose an eligible distinct account for a settled, same-project author."""
        if self._invalid_strict_maximum(maximum):
            return None
        snapshots = dict(usage_snapshots or {})
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
            now = _utc_time()
            day = _utc_day(now)
            choices = [
                name for name in sorted(self.eligible_projects)
                if self.accounts[name].account_id != row[0]
                and (name == project or name not in active_projects)
                and self._qualified_headroom(db, name, day, maximum, snapshots, request_not_after, now) >= maximum
            ]
            return min(choices, key=lambda name: (name == PAID_ACCOUNT, name != project, name),
                       default=None)

    def _new_attempt_accounts(self, db, project, active_projects, maximum, day, reviewer_project, snapshots, request_not_after, now):
        if reviewer_project is not None:
            names = [reviewer_project]
        else:
            names = [project, *sorted(PROJECTS - active_projects)]
        headroom = {}
        for name in names:
            if name in self.eligible_projects:
                budget = self.accounts[name]
                available = self._qualified_headroom(
                    db, name, day, maximum, snapshots, request_not_after, now)
                if available >= maximum:
                    headroom[budget] = available
        if (reviewer_project is None and PAID_ACCOUNT in self.eligible_projects
                and (active_projects != PROJECTS or not headroom)):
            paid = self.accounts[PAID_ACCOUNT]
            available = paid._headroom(db, day)
            if available >= maximum:
                headroom[paid] = available
        return headroom

    def can_start(self, project, active_projects, maximum, *, reviewer_project=None, now=None,
                  usage_snapshots=None, request_not_after=None):
        """Check current ledger capacity before native dispatch.

        The request still needs its atomic reservation at the transport boundary.
        """
        if (project not in active_projects or not self.eligible_projects
                or self._invalid_strict_maximum(maximum)):
            return False
        snapshots = dict(usage_snapshots or {})
        with self.accounts[project]._transaction() as db:
            now = _utc_time(now)
            return bool(self._new_attempt_accounts(
                db, project, active_projects, maximum, _utc_day(now), reviewer_project,
                snapshots, request_not_after, now))

    def reserve_request(self, project: str, attempt_id: str, request_id: str,
                        maximum: int, active_projects: set[str], *,
                        request_sha256: str,
                        review_of: str | None = None, reviewer_project: str | None = None,
                        now: datetime | None = None, usage_snapshots=None,
                        request_not_after=None) -> WorkersAIBudget:
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
        snapshots = dict(usage_snapshots or {})
        with preferred._transaction() as db:
            now = _utc_time(now)
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
                        if self.strict_routes is not None and self._qualified_headroom(
                                db, name, day, maximum, snapshots, request_not_after, now) < maximum:
                            raise BudgetRefused('No eligible account has sufficient daily budget')
                        budget._reserve(db, request_id, maximum, day)
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

            headroom = self._new_attempt_accounts(
                db, project, active_projects, maximum, day, reviewer_project,
                snapshots, request_not_after, now)
            if not headroom:
                raise BudgetRefused('No eligible account has sufficient daily budget')
            selected = max(headroom, key=headroom.__getitem__)
            selected._reserve(db, request_id, maximum, day)
            db.execute('INSERT INTO assignments VALUES (?, ?, ?, ?)',
                       (attempt_id, project, selected.account_id, day))
            db.execute('INSERT INTO routed_requests VALUES (?, ?, ?)',
                       (request_id, attempt_id, request_sha256))
            if review_of is not None:
                db.execute('INSERT INTO independent_reviews VALUES (?, ?)', (attempt_id, review_of))
            return selected
