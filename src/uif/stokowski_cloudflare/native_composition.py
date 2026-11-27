"""Protected composition for the first OffMenu authority installation.

This module plugs into the existing launcher. It does not select issues, create
workspaces, start a second worker, recover old local claims, or own a repository
credential. The issue owner's approved reconstruction and the server-side grant
must independently agree before the native dispatch can consume a retry.
"""
import asyncio
import ctypes
from base64 import b64encode
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from functools import wraps
from datetime import datetime, timedelta
from hashlib import sha1, sha256
from pathlib import Path
from secrets import token_hex
from tempfile import NamedTemporaryFile
import subprocess
import os
import re
import stat
import time
from urllib.parse import urlsplit
from uuid import UUID

from ..effect_authority import AuthorityIdentity, ClaimFence, Completion, Operation, Scope, SourcePin, pinned_sha
from ..handoff_store import Hold, decode, digest, require, wire
from ..shared_issue_coordinator import _lease
from ..quota_handoff import LinearKey
from ..provider_discovery import Adapter, NormalProviderDiscovery
from ..provider_pool import ProviderRoute, RouteAvailability
from .account_admission import FreeRoute
from .budget import BudgetRefused
from .client import FreeAdmissionWindow, GLM_MODEL
from .account_admission import allows_request
from .client import MODEL_RESERVATIONS
from . import client as workers_ai_client
from .remote_authority import AccessTransport, NativeAssignment, RemoteAuthority, protected_record
from .remote_source import SourceTools, pinned_members, review_diff

# The installed native caller has fixed, reviewed source bytes. Only these
# direct public read methods can reach its original authenticated client.
_LINEAR_READS = frozenset({'fetch_candidate_issues', 'fetch_issue', 'fetch_issue_snapshots',
    'fetch_issue_states_by_ids', 'fetch_issues_by_states', 'fetch_comments', 'fetch_team_labels', 'close'})


def _preserve(directory, filename, content):
    """Save an original under the controller's private clone without replacing it."""
    directory.mkdir(mode=0o700, exist_ok=True)
    require(directory == directory.resolve(strict=True) and directory.is_dir()
            and not directory.stat().st_mode & 0o077, 'authority_private_receipt_directory')
    destination = directory / filename
    with NamedTemporaryFile(dir=directory) as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
        try:
            os.link(stream.name, destination)
        except FileExistsError:
            require(destination.resolve(strict=True) == destination and destination.is_file()
                    and destination.read_bytes() == content, 'authority_original_receipt_conflict')
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return destination


def _clock_file_identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _read_gate_clock(fd, name):
    """Read one exact bounded, stable original sidecar without following a link."""
    with os.fdopen(os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd), 'rb') as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid()
                and stat.S_IMODE(before.st_mode) == 0o600 and before.st_nlink == 1
                and 0 < before.st_size <= 8192, 'authority_waiting_gate_clock_custody')
        raw = stream.read(8193)
        require(len(raw) == before.st_size and _clock_file_identity(before) ==
                _clock_file_identity(os.fstat(stream.fileno())) ==
                _clock_file_identity(os.stat(name, dir_fd=fd, follow_symlinks=False)),
                'authority_waiting_gate_clock_changed')
        return raw


def _publish_gate_clock(fd, name, raw):
    require(len(raw) <= 8192, 'authority_waiting_gate_clock_custody')
    _publish_private_original(fd, name, raw, prefix='.gate-clock-')


def _publish_private_original(fd, name, raw, *, prefix):
    """Make a complete, fsynced record visible once, with no transient hardlink."""
    temp = prefix + token_hex(16)
    handle = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=fd)
    try:
        with os.fdopen(handle, 'wb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        rename = ctypes.CDLL(None, use_errno=True).renameat2
        rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
        rename.restype = ctypes.c_int
        if rename(fd, os.fsencode(temp), fd, os.fsencode(name), 1):
            number = ctypes.get_errno()
            raise OSError(number, 'authority_waiting_gate_clock_publish')
        os.fsync(fd)
    finally:
        try:
            os.unlink(temp, dir_fd=fd)
        except FileNotFoundError:
            pass


HANDOFF_RECEIPT_LIMIT = 2 * 1024 * 1024
HANDOFF_BUNDLE_LIMIT = 8 * 1024 * 1024


@dataclass(frozen=True)
class _RetainedHandoffOriginal:
    path: Path
    producer_sha256: str


@dataclass(frozen=True)
class NativeOwnerCandidateEvidence:
    """Verified producer-side observation, not an authorization or wire envelope."""

    raw: bytes
    handoff_sha256: str
    fence: ClaimFence


@contextmanager
def _handoff_directory(directory):
    """Open one fixed private owner handoff directory."""
    require(directory == directory.resolve(strict=True), 'authority_handoff_receipt_directory')
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        root = os.fstat(fd)
        require(root.st_uid == os.geteuid() and stat.S_IMODE(root.st_mode) == 0o700,
                'authority_handoff_receipt_directory')
        yield fd
        require(_clock_file_identity(root)[:6] == _clock_file_identity(directory.lstat())[:6],
                'authority_handoff_receipt_directory_changed')
    finally:
        os.close(fd)


def _retained_handoff_bytes(fd, name, limit):
    """Read one stable bounded member of the fixed private journal."""
    handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(handle, 'rb') as stream:
        start = os.fstat(stream.fileno())
        require(stat.S_ISREG(start.st_mode) and start.st_uid == os.geteuid()
                and stat.S_IMODE(start.st_mode) == 0o600 and start.st_nlink == 1
                and 0 < start.st_size <= limit, 'authority_handoff_receipt_custody')
        raw = stream.read(limit + 1)
        require(len(raw) == start.st_size and _clock_file_identity(start) ==
                _clock_file_identity(os.fstat(stream.fileno())) ==
                _clock_file_identity(os.stat(name, dir_fd=fd, follow_symlinks=False)),
                'authority_handoff_receipt_changed')
    return raw


def _retained_handoff_sha(fd, name, limit):
    return sha256(_retained_handoff_bytes(fd, name, limit)).hexdigest()


def _retain_handoff_original(directory, name, raw, limit):
    """Private producer commitment; verify existing exact bytes on collision."""
    require(0 < len(raw) <= limit, 'authority_handoff_receipt_custody')
    directory.mkdir(mode=0o700, exist_ok=True)
    pin = sha256(raw).hexdigest()
    with _handoff_directory(directory) as fd:
        try:
            _publish_private_original(fd, name, raw, prefix='.owner-handoff-')
        except FileExistsError:
            pass  # Reconcile only exact bounded existing bytes.
        require(_retained_handoff_sha(fd, name, limit) == pin,
                'authority_original_handoff_bytes_changed')
        os.fsync(fd)
    return _RetainedHandoffOriginal(directory / name, pin)


def _verify_producer_handoff(original, directory, candidate):
    require(type(original) is _RetainedHandoffOriginal and type(candidate) is str
            and re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', candidate)
            and original.path == directory / (candidate + '.json'),
            'authority_handoff_receipt_target')
    with _handoff_directory(directory) as fd:
        pin = _retained_handoff_sha(fd, original.path.name, HANDOFF_RECEIPT_LIMIT)
        require(pin == original.producer_sha256, 'authority_original_handoff_bytes_changed')
    return pin


def _verify_fresh_tree(path, head, *, git_env=None):
    tree = subprocess.check_output(['git', '--no-optional-locks', '-C', str(path), 'ls-tree',
                '-r', '-z', '--full-tree', head], timeout=15, stderr=subprocess.DEVNULL, env=git_env)
    for entry in tree.rstrip(b'\0').split(b'\0') if tree else ():
        fields, name = entry.split(b'\t', 1)
        mode, kind, oid = fields.split(b' ')
        file = path / os.fsdecode(name)
        require(file.parent.resolve(strict=True) == file.parent and kind == b'blob',
                'authority_source_tree_member')
        info = file.lstat()
        if mode == b'120000':
            require(stat.S_ISLNK(info.st_mode), 'authority_source_tree_mode')
            link = os.fsencode(os.readlink(file))
            actual = sha1(b'blob ' + str(len(link)).encode() + b'\0' + link).hexdigest()
        else:
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                    and (b'100755' if info.st_mode & 0o111 else b'100644') == mode,
                    'authority_source_tree_mode')
            blob = sha1(b'blob ' + str(info.st_size).encode() + b'\0')
            with file.open('rb') as stream:
                for chunk in iter(lambda: stream.read(65536), b''):
                    blob.update(chunk)
            actual = blob.hexdigest()
        require(actual.encode() == oid, 'authority_source_tree_bytes')


def _verify_source_checkout(path, source, git_config_sha256, review_postimage=None, *, git_env=None):
    # Check bytes BEFORE Git interprets repository-local settings. A shared
    # clone, a changed filter/credential helper or an executable hook is
    # not a source pin. The installer qualifies the specific config bytes.
    git_dir = path / '.git'
    cfg = git_dir / 'config'
    require(git_dir.is_dir() and git_dir.resolve() == git_dir and cfg.is_file()
            and cfg.resolve() == cfg and cfg.stat().st_nlink == 1
            and sha256(cfg.read_bytes()).hexdigest() == git_config_sha256
            and not (git_dir / 'commondir').exists()
            and not (git_dir / 'objects/info/alternates').exists()
            and not (git_dir / 'info/grafts').exists()
            and not (git_dir / 'refs/replace').exists(), 'authority_private_git_config')
    hooks = git_dir / 'hooks'
    require(not hooks.exists() or hooks.resolve() == hooks and all(
        f.is_file() and not f.is_symlink() and f.name.endswith('.sample')
        for f in hooks.iterdir()), 'authority_private_git_hooks')
    def git(*args):
        return subprocess.check_output(['git', '--no-optional-locks', '-C', str(path), *args],
                    timeout=3, stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True, env=git_env).strip()
    origins = {f'https://github.com/{source.repository}.git', f'https://github.com/{source.repository}',
               f'git@github.com:{source.repository}.git'}
    require(git('rev-parse','--absolute-git-dir') == str(path / '.git')
            and git('rev-parse','--path-format=absolute','--git-common-dir') == str(path / '.git')
            and git('remote','get-url','origin') in origins
            and git('merge-base', source.base, source.head) == source.base, 'authority_source_changed')
    observed = (git('rev-parse','HEAD'), git('rev-parse','HEAD^{tree}'), git('branch','--show-current'))
    if observed != (source.head, source.tree, source.branch):
        require(review_postimage is not None and observed == review_postimage
                and git('show','-s','--format=%P','HEAD') == source.head,
                'authority_source_changed')
    return path

def observe_clean_assignment_workspace(path: Path, source: SourcePin, git_config_sha256: str):
    """Sequential, non-atomic checkout observation under exclusive trusted custody.

    Caller must already control the quiescent checkout and verified Git config.
    This does not create a lock, assignment, authority or atomic snapshot.
    """
    pinned_sha(git_config_sha256)
    require(path.is_absolute() and path.resolve(strict=True) == path and path.is_dir(),
            'authority_workspace_identity')
    info = path.stat()
    require(not info.st_mode & 0o077, 'authority_isolated_claim_workspace')

    # Ignore ambient Git config, object directories and SSH/git overrides.
    # The local config itself is still the caller's independently reviewed pin.
    env = {'PATH': os.defpath, 'LC_ALL': 'C', 'GIT_CONFIG_NOSYSTEM': '1',
           'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_NO_REPLACE_OBJECTS': '1',
           'GIT_NO_LAZY_FETCH': '1', 'GIT_ALLOW_PROTOCOL': '', 'GIT_TERMINAL_PROMPT': '0'}

    def observe():
        _verify_source_checkout(path, source, git_config_sha256, git_env=env)
        state = subprocess.check_output(['git', '--no-optional-locks', '-c', 'core.fsmonitor=false',
                      '-C', str(path), 'status', '--porcelain=v1', '-z', '--ignored',
                      '--untracked-files=all'], timeout=10, stdin=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL, env=env)
        require(not state and not (path / '.stokowski').exists(),
                'authority_dirty_workspace_needs_original_reconstruction')
        _verify_fresh_tree(path, source.head, git_env=env)

    # Qualify before walking any contents, then bracket a repeated validation
    # with matching inventories plus a recheck of checkout directory identity.
    observe()
    prepared = workspace_recovery_digest(path)
    observe()
    final_prepared = workspace_recovery_digest(path)
    final = path.stat()
    require(prepared == final_prepared, 'authority_private_initial_bytes_changed')
    require((info.st_dev, info.st_ino, info.st_mode) == (final.st_dev, final.st_ino, final.st_mode)
            and path.resolve(strict=True) == path, 'authority_isolated_claim_workspace')
    return {'path': str(path), 'device': info.st_dev, 'inode': info.st_ino,
            'git_config_sha256': git_config_sha256, 'prepared_tree_sha256': prepared}


def workspace_recovery_digest(path):
    """Exact read-only inventory of retained private files and Git checkout metadata."""
    entries = []
    def add_file(p):
        info = p.lstat()
        if stat.S_ISLNK(info.st_mode):
            value = {'link': os.readlink(p)}
        else:
            require(stat.S_ISREG(info.st_mode), 'authority_recovery_special_file')
            h = sha256()
            with p.open('rb') as stream:
                for chunk in iter(lambda: stream.read(65536), b''):
                    h.update(chunk)
            value = {'file_sha256': h.hexdigest()}
        entries.append([p.relative_to(path).as_posix(), stat.S_IMODE(info.st_mode), value])
    for folder, dirs, files in os.walk(path, followlinks=False):
        directory = Path(folder)
        dirs[:] = sorted(d for d in dirs if not (directory == path and d == '.git'))
        for name in dirs.copy():
            if (directory / name).is_symlink():
                add_file(directory / name)
                dirs.remove(name)
        for name in sorted(files):
            add_file(directory / name)
    for part in ('HEAD', 'index', 'config', 'packed-refs'):
        p = path / '.git' / part
        if p.exists():
            add_file(p)
    return digest(sorted(entries))


class NativeComposition:
    def __init__(self, remote, path):
        self.remote, self.path = remote, Path(path)
        self._running = ContextVar('shared_native_effect_assignment', default=None)
        self._slot = ContextVar('native_original_linear_effect', default=None)
        self._seen_fences = {}
        self._renew_after = {}
        self._private_postimages = {}
        self._receiving_after = {}
        self.remote.retain_intent = self.retain_intent
        self._provider_discovery = None

    def installation(self):
        record = protected_record(self.path)
        require(record['schema'] == 'uif.stokowski-remote-authority/1'
                and record['origin'].rstrip('/') == 'https://' + self.remote.transport.host
                and Path(record['access_credential']) == self.remote.transport._credential_file
                and record['native_identity'] == asdict(self.remote.identity)
                and record['quota_issuer'] == asdict(self.remote.quota_issuer)
                and record['route'] == asdict(self.remote.route)
                and record.get('reviewer_route') == (asdict(self.remote.routes['UIF']) if 'UIF' in self.remote.routes else None)
                and record['workspace_id'] == self.remote.workspace_id
                and record['project_id'] == self.remote.project_id
                and record['repository_id'] == self.remote.repository_id, 'authority_installation_changed')
        return record

    def startup(self, unit, stage):
        """Fresh TLS receipt for one exact launcher challenge, never a lease."""
        config = self.installation()
        expected = (unit, stage, self.remote.identity.authority_id, self.remote.identity.session_id,
                    config['coordinator_epoch'], self.remote.identity.deployment_sha256,
                    self.remote.identity.policy_sha256)
        require((unit, stage) == (config['unit'], config['stage']), 'authority_start_target')
        challenge = token_hex(24)
        start = time.monotonic()
        receipt = self.remote.transport.request('/v1/startup/read', {'unit': unit, 'stage': stage,
                                                'nonce': challenge}, timeout=4)
        fields = ('unit','stage','authority_id','session_id','coordinator_epoch',
                  'deployment_sha256','policy_sha256')
        now = self.remote.clock().timestamp()
        require(tuple(receipt.get(k) for k in fields) == expected and receipt.get('nonce') == challenge
                and type(receipt.get('expires_at')) is int and now < receipt['expires_at'] <= now + 5
                and time.monotonic() - start < 5, 'authority_start_receipt')
        return expected, receipt['expires_at']

    def startup_arguments(self):
        conf = self.installation()
        unit, stage = conf['unit'], conf['stage']
        binding = (unit, stage, self.remote.identity.authority_id, self.remote.identity.session_id,
                   conf['coordinator_epoch'], self.remote.identity.deployment_sha256,
                   self.remote.identity.policy_sha256)

        def verifier(request):
            schema, wanted, deadline = request
            require(schema == 'uif.stokowski.native-start/1' and wanted == binding
                    and time.monotonic_ns() < deadline, 'authority_start_challenge')
            verified, expires = self.startup(unit, stage)
            remaining = expires - self.remote.clock().timestamp()
            valid_until = min(deadline, time.monotonic_ns() + int(remaining * 1_000_000_000))
            require(time.monotonic_ns() < valid_until and verified == wanted, 'authority_start_expired')
            return request, verified, 'mounted', valid_until
        return dict(startup_binding=binding, startup_verifier=verifier)

    def assignment(self, project, issue_id, scope_sha):
        if project != 'OffMenu':
            return None
        installation = self.installation()
        data = installation['assignments'].get(issue_id)
        if data is None or data['scope_sha256'] != scope_sha:
            return None
        key = LinearKey(self.remote.workspace_id, self.remote.project_id, issue_id)
        return NativeAssignment(Scope(self.remote.repository_id, key), SourcePin(**data['source']),
             data['reconstruction_sha256'], scope_sha, installation['coordinator_epoch'], tuple(data['attempts']),
             _lease(data['previous']) if data.get('previous') else None, data.get('previous_completion_sha256'))

    def selected(self, project, issue, scope_sha):
        return self.assignment(project, issue.id, scope_sha) is not None

    def verify_workspace(self, owner, issue, grant, *, review_postimage=None):
        # Native preparation must not switch an unrelated/dirty worktree or
        # recreate a missing candidate from a connector's hash assertion.
        root = owner.cfg.workspace.resolved_root().resolve(strict=True)
        # Only a per-original private clone is writable. A shared checkout or a
        # linked worktree could mutate another owner's Git index or refs.
        claim_id = grant.claim(self.remote.identity).request_id
        require(root == Path(self.installation()['workspace_root']), 'authority_isolated_claim_workspace')
        path = root / issue.identifier
        require(path.parent == root and path.resolve(strict=True) == path, 'authority_workspace_identity')
        item = self.installation()['assignments'][issue.id]
        info = path.stat()
        require(item['workspace_binding'] == {'original_claim_id': claim_id, 'path': str(path),
                        'device': info.st_dev, 'inode': info.st_ino} and not info.st_mode & 0o077,
                'authority_isolated_claim_workspace')
        return _verify_source_checkout(path, grant.source, item['git_config_sha256'], review_postimage)

    def _review_postimage(self, owner, issue, grant, record):
        if not record.get('candidate_tree'):
            return None
        path = owner.cfg.workspace.resolved_root() / issue.identifier / '.stokowski/review-binding.json'
        if not path.is_file():
            return None  # original normal entry precedes the native checkpoint
        require(path.resolve(strict=True) == path, 'authority_review_checkpoint_path')
        binding = decode(path.read_bytes(), limit=64_000)
        require(binding['issue_id'] == issue.id and binding['kind'] == 'git'
                and binding['scope_sha256'] == grant.scope_sha256
                and binding['state'] == 'independent-review'
                and binding['run'] == owner._issue_state_runs.get(issue.id, 1),
                'authority_original_review_binding')
        return (binding['candidate'], record['candidate_tree'], binding['branch'])

    def _first_workspace(self, path, grant, existing):
        # New native execution may never borrow dirty files, ignored files,
        # another claim's private settlement, index or recovered evidence.
        # Within the same process native-owned changes survive stage switches
        # only on the independently read current fence.
        issue_id = grant.scope.key.issue_id
        if existing is not None and self._seen_fences.get(issue_id) == existing.lease.fence:
            require(self._private_postimages.get(issue_id) == workspace_recovery_digest(path),
                    'authority_private_checkpoint_changed')
            return
        state = subprocess.check_output(['git', '--no-optional-locks', '-C', str(path), 'status',
                    '--porcelain=v1', '-z', '--ignored', '--untracked-files=all'], timeout=10,
                    stderr=subprocess.DEVNULL)
        if not state and not (path / '.stokowski').exists():
            _verify_fresh_tree(path, grant.source.head)
            # Includes raw checkout bytes, index and file modes. Git status
            # alone misses skip-worktree and assume-unchanged differences.
            require(self.installation()['assignments'][issue_id]['prepared_tree_sha256']
                    == workspace_recovery_digest(path), 'authority_private_initial_bytes_changed')
            return
        expected = self.installation()['assignments'][grant.scope.key.issue_id].get('recovered_tree_sha256')
        require(existing is not None and expected and expected == workspace_recovery_digest(path),
                'authority_dirty_workspace_needs_original_reconstruction')


    @contextmanager
    def bind_dispatch(self, owner, issue, scope_sha, record):
        grant = self.assignment(owner.project_name, issue.id, scope_sha)
        require(grant is not None and record['attempt'] in grant.attempts, 'authority_attempt_not_selected')
        role = self.attempt_account(grant, record['attempt'])
        expected_review = self.installation()['assignments'][issue.id].get('review_of', {}).get(record['attempt'])
        require(record.get('review_of') == expected_review
                and record.get('reviewer_project') == ('UIF' if role == 'UIF' else None),
                'authority_review_author_changed')
        expected = self._review_postimage(owner, issue, grant, record) if role == 'UIF' else None
        path = self.verify_workspace(owner, issue, grant, review_postimage=expected)
        prior = None if grant.previous is not None else self.remote.read_claim(owner.project_name, issue.id, False)
        if grant.previous is not None:
            # The successor starts on separately prepared bytes. If it is
            # already issued, recover its exact original for later stages.
            try:
                original = self.remote.original_lease(grant)
            except Hold as error:
                if str(error) != 'authority_original_reclaim_unverified':
                    raise
            else:
                prior = self.remote.read_claim(owner.project_name, issue.id, False)
                require(prior is not None and prior.lease.fence == original.fence, 'authority_original_reclaim_changed')
        key = grant.claim(self.remote.identity).request_id
        evidence = path / '.git/uif-original-effects'
        require(not (evidence / (key + '.receiving')).exists() or (evidence / (key + '.received')).exists(),
                'authority_original_handoff_pending')
        require(not (evidence / (key + '.settled')).exists() or (evidence / (key + '.released')).exists(),
                'authority_original_release_pending')
        self._first_workspace(path, grant, prior)
        claim = self.remote.acquire(grant, record['attempt'])
        self._seen_fences[issue.id] = claim.lease.fence
        self._private_postimages[issue.id] = workspace_recovery_digest(path)
        binding = self._running.set((grant, claim))
        try:
            yield
        finally:
            self._running.reset(binding)

    @contextmanager
    def settlement_context(self, owner, issue, attempt):
        record = getattr(attempt, 'settlement_record', None)
        require(owner.running.get(issue.id) is attempt and isinstance(record, dict)
                and record.get('issue_id') == issue.id and record.get('state') == attempt.state_name
                and record.get('session_id') == attempt.session_id and record.get('run') ==
                owner._issue_state_runs.get(issue.id, 1), 'authority_original_settlement_identity')
        pair = self._running.get()
        if pair is None:
            grant = self.assignment(owner.project_name, issue.id, record.get('scope_sha256'))
            require(grant is not None and attempt.session_id in grant.attempts, 'authority_recovery_grant')
            claim = self.remote.read_claim(owner.project_name, attempt.session_id, True)
            require(claim is not None and claim.lease.fence.scope == grant.scope
                    and claim.lease.fence.source == grant.source
                    and claim.lease.fence.coordinator_epoch == grant.expected_coordinator_epoch,
                    'authority_recovery_fence')
            require(self.remote.original_lease(grant).fence == claim.lease.fence,
                    'authority_original_claim_not_current')
            expected = None
            if attempt.state_name == 'independent-review':
                author = self.installation()['assignments'][issue.id].get('review_of', {}).get(attempt.session_id)
                require(author in grant.attempts, 'authority_recovery_review_author')
                proof = self.remote.transport.request('/v1/review/read', {'current': asdict(claim.lease),
                    'review_attempt_id': attempt.session_id, 'author_attempt_id': author})
                candidate = proof['candidate']
                require(proof['fence'] == asdict(claim.lease.fence)
                        and digest(candidate) == proof['candidate_sha256']
                        and candidate['source'] == asdict(grant.source) and candidate['issue_id'] == issue.id
                        and candidate['scope_sha256'] == grant.scope_sha256,
                        'authority_recovery_review_candidate')
                expected = self._review_postimage(owner, issue, grant, candidate)
            path = self.verify_workspace(owner, issue, grant, review_postimage=expected)
            self._first_workspace(path, grant, claim)
            pair = (grant, claim)
        require(pair[0].scope.key.issue_id == issue.id and pair[1].attempt_id == attempt.session_id,
                'authority_late_settlement')
        value = self._running.set(pair)
        try:
            yield pair
        finally:
            self._running.reset(value)

    def source_spec(self, issue_id, author):
        return self.installation()['assignments'].get(issue_id, {}).get('source_overlays', {}).get(author)

    def supports_source(self, issue_id, record):
        spec = self.source_spec(issue_id, record['attempt'])
        if spec is None:
            return False
        members = pinned_members(spec, record['write_roots'])
        return all(pin['writable'] or path in record['read_paths']
                   or any(path.startswith(root + '/') for root in record.get('read_roots', ()))
                   for path, pin in members.items())

    def source_receiver(self, issue_id, author):
        spec = self.source_spec(issue_id, author['attempt'])
        require(spec and self.supports_source(issue_id, author), 'authority_source_receiver_unavailable')
        return SourceTools(self.remote, self.current, issue_id, author['attempt'],
                           pinned_members(spec, author['write_roots']),
                           spec['public_source_receipt_sha256'])

    def checkpoint_source(self, issue_id, author):
        """Freeze the exact DO-owned result. The clean Foo clone stays at source."""
        receiver = self.source_receiver(issue_id, author)
        claim = self.current(issue_id)
        registered = receiver.list(claim)
        revision = registered['revision']
        require(revision > 0, 'authority_source_no_candidate')
        reply = self.remote.transport.request('/v1/source/checkpoint', {
            'current': asdict(claim.lease), 'attempt_id': author['attempt'], 'revision': revision})
        artifact = reply['candidate']
        require(artifact['schema'] == 'uif.source-candidate/1' and artifact['revision'] == revision
                and artifact['fence'] == asdict(claim.lease.fence) and artifact['source'] == asdict(claim.lease.fence.source)
                and artifact['public_source_receipt_sha256'] == receiver.source_receipt
                and artifact['author_attempt_id'] == author['attempt'] and reply['candidate_sha256'] == digest(artifact),
                'authority_source_checkpoint_changed')
        return reply, review_diff(artifact, receiver.members)

    def retain_source_checkpoint(self, issue_id, author):
        def finish(request_id, content):
            receiver = self.source_receiver(issue_id, author)
            if receiver.list()['revision'] == 0:
                return
            checkpoint, _ = self.checkpoint_source(issue_id, author)
            spec = self.installation()['assignments'][issue_id]
            path = Path(spec['workspace_binding']['path']) / '.git/uif-original-effects'
            receipt = {'schema': 'uif.native-source-checkpoint/1', 'issue_id': issue_id,
                       'author_attempt_id': author['attempt'], 'request_id': request_id,
                       'report_sha256': sha256(content.encode()).hexdigest(),
                       'revision': checkpoint['candidate']['revision'],
                       'candidate_sha256': checkpoint['candidate_sha256']}
            _preserve(path, digest({'author': author['attempt']}) + '.checkpoint', wire(receipt))
        return finish

    def prepare_source_review(self, issue_id, author, record):
        if not self.source_spec(issue_id, author['attempt']):
            return record
        checkpoint, diff = self.checkpoint_source(issue_id, author)
        value = checkpoint['candidate_sha256']
        prompt = (record['prompt'] + '\nAuthority-owned candidate SHA-256: ' + value +
                  '\nExact UTF-8 diff against the pinned source follows. The unchanged Foo checkout '
                  'is the base. This candidate awaits the sole OffMenu publisher and its required '
                  'checks. Record the candidate SHA-256 in routing.source_candidate_sha256.\n' + diff)
        require(len(prompt.encode()) <= 64_000, 'authority_source_review_prompt_size')
        return dict(record, source_candidate_sha256=value, prompt=prompt)

    def retain_final_review(self, request_id, content):
        grant, original = self._running.get()
        config = self.installation()['assignments'][grant.scope.key.issue_id]
        author = config.get('review_of', {}).get(original.attempt_id)
        require(author in grant.attempts, 'authority_final_review_owner')
        directory = Path(config['workspace_binding']['path']) / '.git/uif-original-effects'
        intent_file = directory / (request_id + '.json')
        require(intent_file == intent_file.resolve(strict=True), 'authority_final_request_original')
        intent = decode(intent_file.read_bytes(), limit=8192)
        op = intent['operation']
        require(op['fence'] == asdict(original.lease.fence) and op['original_id'] == request_id
                and intent['attempt_id'] == original.attempt_id and
                intent['route'] == asdict(self.remote.routes['UIF']), 'authority_final_reviewer_route')
        operation = Operation(original.lease.fence, request_id, op['payload_sha256'])
        response = self.remote.provider_result(operation)
        raw = response.read()
        message = decode(raw, limit=256*1024)['choices'][0]['message']
        require(not message.get('tool_calls') and message.get('content') == content,
                'authority_final_review_content')
        fenced = re.search(r'```json\s*(.*?)\s*```', content, re.IGNORECASE | re.DOTALL)
        report = decode((fenced.group(1) if fenced else content).encode(), limit=64_000)
        fact = {'schema': 'uif.native-review-result/1', 'attempt_id': original.attempt_id,
                'author_attempt_id': author, 'operation': op, 'receipt_sha256': response.receipt_sha256,
                'body_sha256': sha256(raw).hexdigest(), 'report_sha256': digest(report)}
        return _preserve(directory, digest({'review': original.attempt_id}) + '.reviewed', wire(fact))

    def source_publisher_artifact(self, issue, record, path):
        """Bind a separately reviewed overlay to the existing sole publisher."""
        grant, claim = self._running.get()
        spec = self.installation()['assignments'][issue.id]
        require(spec.get('publisher') == 'offmenu-cloud' and spec.get('publisher_target') ==
                {'repository': grant.source.repository, 'base': grant.source.branch}
                and str(path) == spec['workspace_binding']['path'], 'authority_source_publisher_custody')
        author = spec.get('review_of', {}).get(claim.attempt_id)
        source = self.source_spec(issue.id, author)
        require(source is not None, 'authority_source_author_receipt')
        proof = self.remote.transport.request('/v1/review/read', {
            'current': asdict(self.current(issue.id).lease), 'review_attempt_id': claim.attempt_id,
            'author_attempt_id': author})
        require(proof['fence'] == asdict(claim.lease.fence) and digest(proof['candidate']) == proof['candidate_sha256'],
                'authority_source_reviewer_receipt')
        expected = proof['candidate']['source_candidate_sha256']
        review = record['accepted_review']
        routing = review['routing']
        require(expected == routing.get('source_candidate_sha256') and record['binding']['issue_id'] == issue.id
                and record['binding']['scope_sha256'] == grant.scope_sha256
                and review.get('verdict') == 'approve', 'authority_source_accepted_review')
        # The reviewer cannot change an already frozen source revision. Read it
        # directly under the original author and same lease for the publisher.
        members = source['members']
        tool = SourceTools(self.remote, self.current, issue.id, author, members,
                           source['public_source_receipt_sha256'])
        current = self.current(issue.id)
        listed = tool.list(current)
        require(listed['sealed'], 'authority_source_checkpoint_not_sealed')
        reply = self.remote.transport.request('/v1/source/checkpoint', {
            'current': asdict(current.lease), 'attempt_id': author, 'revision': listed['revision']})
        artifact = reply['candidate']
        require(expected == reply['candidate_sha256'] == digest(artifact) and
                artifact['fence'] == asdict(claim.lease.fence) and artifact['source'] == asdict(grant.source)
                and artifact['author_attempt_id'] == author, 'authority_source_publisher_candidate_changed')
        review_diff(artifact, members)
        review_path = path / '.git/uif-original-effects' / (digest({'review': claim.attempt_id}) + '.reviewed')
        require(review_path == review_path.resolve(strict=True), 'authority_review_output_path')
        output = decode(review_path.read_bytes(), limit=8192)
        require(output['attempt_id'] == claim.attempt_id and output['author_attempt_id'] == author
                and output['report_sha256'] == digest(review) and output['operation']['fence'] == asdict(claim.lease.fence),
                'authority_review_output_binding')
        op = Operation(claim.lease.fence, output['operation']['original_id'], output['operation']['payload_sha256'])
        actual = self.remote.provider_result(op)
        require(actual.receipt_sha256 == output['receipt_sha256'] and
                sha256(actual.read()).hexdigest() == output['body_sha256'], 'authority_review_output_changed')
        directory = path / '.git/uif-owner-handoffs'
        receipt = {'schema': 'uif.source-overlay-publisher-handoff/1', 'publisher': 'offmenu-cloud',
                   'target': spec['publisher_target'], 'issue_id': issue.id, 'candidate': artifact,
                   'candidate_sha256': expected, 'accepted_review': review, 'binding': record['binding'],
                   'review_input_binding_sha256': proof['candidate_sha256'], 'review_output': output}
        return _retain_handoff_original(directory, expected + '.json', wire(receipt), HANDOFF_RECEIPT_LIMIT)

    def review_candidate(self, issue_id, record, final_prompt):
        grant, _ = self._running.get()
        claim = self.current(issue_id)
        author, reviewer = record['review_of'], record['attempt']
        candidate = {'kind':'uif.native-review-candidate/1', 'issue_id': issue_id,
                           'scope_sha256': record['scope_sha256'], 'source': asdict(grant.source),
                           'review_attempt_id': reviewer, 'author_attempt_id': author,
                           'candidate_tree': record.get('candidate_tree'),
                           'review_prompt_sha256': sha256(record['prompt'].encode()).hexdigest()}
        if record.get('source_candidate_sha256') is not None:
            candidate['source_candidate_sha256'] = record['source_candidate_sha256']
        expected = digest(candidate)
        reply = self.remote.transport.request('/v1/review/read',
                    {'current': asdict(claim.lease), 'review_attempt_id': reviewer, 'author_attempt_id': author})
        require(reply['candidate_sha256'] == expected and reply['fence'] == asdict(claim.lease.fence)
                and reply['send_prompt_sha256'] == sha256(final_prompt.encode()).hexdigest(),
                'authority_review_candidate_changed')

    def retain_intent(self, intent):
        pair = self._running.get()
        require(pair is not None and pair[1].lease.fence == intent.operation.fence and
                pair[1].attempt_id == intent.attempt_id, 'authority_provider_original_context')
        item = self.installation()['assignments'][pair[0].scope.key.issue_id]
        directory = Path(item['workspace_binding']['path']) / '.git/uif-original-effects'
        return _retain_handoff_original(directory, intent.operation.original_id + '.json',
                                        wire(asdict(intent)), 8192).path

    def publisher_handoff(self, path, issue, record):
        """Export exactly one already accepted candidate for its existing sole publisher."""
        claim = self.current(issue.id)
        binding = record['binding']
        grant, _ = self._running.get()
        install = self.installation()['assignments'][issue.id]
        publisher = install.get('publisher')
        require(publisher == 'offmenu-cloud' and install.get('publisher_target') ==
                {'repository': binding['repo'], 'base': binding['base']}
                and path == Path(install['workspace_binding']['path'])
                and binding['kind'] == 'git' and binding['issue_id'] == issue.id
                and binding['repo'] == grant.source.repository and binding['scope_sha256'] == grant.scope_sha256
                , 'authority_publisher_handoff_scope')
        def git(*args):
            return subprocess.check_output(['git', '--no-optional-locks', '-C', str(path), *args],
                stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20).strip()
        head = binding['candidate']
        require(git('rev-parse', 'HEAD').decode() == head
                and git('branch','--show-current').decode() == binding['branch']
                and git('show','-s','--format=%P',head).decode() == grant.source.head,
                'authority_publisher_candidate_parent')
        parent = grant.source.head
        directory = path / '.git/uif-owner-handoffs'
        directory.mkdir(mode=0o700, exist_ok=True)
        # Git writes no ref here. An accepted single-parent commit bundle can
        # be verified/imported by the existing owner's controlled checkout.
        with NamedTemporaryFile(dir=directory) as tmp:
            git('bundle', 'create', tmp.name, 'refs/heads/' + binding['branch'], '^' + parent)
            require(Path(tmp.name).stat().st_size <= 8*1024*1024, 'authority_candidate_bundle_size')
            candidate = Path(tmp.name).read_bytes()
        bundle = _retain_handoff_original(directory, head + '.bundle', candidate, HANDOFF_BUNDLE_LIMIT)
        receipt = {'schema':'uif.native-owner-candidate/1','publisher': publisher,
                   'issue_id':issue.id, 'fence':asdict(claim.lease.fence), 'binding':binding,
                   'accepted_review_sha256':digest(record['accepted_review']), 'candidate':head,
                   'tree':git('rev-parse',head+'^{tree}').decode(), 'parent':parent,
                   'bundle':bundle.path.name, 'bundle_sha256':bundle.producer_sha256,
                   'existing_publication_intent':record.get('publication_intent'),
                   'existing_publication_receipt':record.get('publication_receipt')}
        return _retain_handoff_original(directory, head + '.json', wire(receipt), HANDOFF_RECEIPT_LIMIT)

    def observe_owner_candidate(self, path, issue, candidate, receipt):
        """Project exact ordinary-Git original evidence for protected owner intake.

        This performs existing native status/claim observations only. It does not
        renew, publish or authorize the recipient. The owner must independently
        resolve its current fence before using these bytes.
        """
        pair = self._running.get()
        require(pair is not None and pair[0].scope.key.issue_id == issue.id,
                'authority_native_context')
        grant, original = pair
        require(self.assignment('OffMenu', issue.id, grant.scope_sha256) == grant,
                'authority_native_grant_changed')
        item = self.installation()['assignments'][issue.id]
        directory = path / '.git/uif-owner-handoffs'
        require(path == Path(item['workspace_binding']['path'])
                and type(candidate) is str and re.fullmatch(r'[0-9a-f]{40}', candidate)
                and type(receipt) is _RetainedHandoffOriginal
                and receipt.path == directory / (candidate + '.json'),
                'authority_handoff_receipt_target')
        status = self.remote.status()
        current = self.remote.read_claim('OffMenu', original.attempt_id, True)
        require(not status['stopped'] and status['coordinator_epoch'] == original.lease.fence.coordinator_epoch
                and current is not None and current.attempt_id == original.attempt_id
                and current.lease.fence == original.lease.fence
                and self.remote.clock().timestamp() < current.lease.expires_at,
                'authority_original_lease_changed')
        with _handoff_directory(directory) as fd:
            raw = _retained_handoff_bytes(fd, receipt.path.name, HANDOFF_RECEIPT_LIMIT)
        current_sha = sha256(raw).hexdigest()
        require(current_sha == receipt.producer_sha256, 'authority_handoff_receipt_bytes_changed')
        value = decode(raw, limit=HANDOFF_RECEIPT_LIMIT)
        require(raw == wire(value) and value['schema'] == 'uif.native-owner-candidate/1'
                and value['issue_id'] == issue.id and value['candidate'] == candidate
                and value['fence'] == asdict(current.lease.fence)
                and item.get('publisher') == value['publisher'] == 'offmenu-cloud',
                'authority_native_candidate_original')
        return NativeOwnerCandidateEvidence(raw, current_sha, current.lease.fence)

    def requires_publisher(self, issue_id, selected):
        if selected['binding']['kind'] == 'git':
            return True
        overlays = self.installation()['assignments'][issue_id].get('source_overlays', {})
        # Native sees a clean private checkout even when the actual source
        # effect was a DO-owned candidate. A missing routing tag must never
        # turn such an accepted unit into automatic source-free publication.
        if selected['accepted_review'].get('routing', {}).get('source_candidate_sha256'):
            return True
        if overlays:
            raise Hold('authority_original_external_candidate_receipt_required')
        return False

    async def send_handoff(self, path, issue, reviewed):
        review_sha = reviewed['accepted_review'].get('routing', {}).get('source_candidate_sha256')
        if review_sha:
            receipt = await asyncio.to_thread(self.source_publisher_artifact, issue, reviewed, path)
            candidate = review_sha
        else:
            receipt = await asyncio.to_thread(self.publisher_handoff, path, issue, reviewed)
            candidate = reviewed['binding']['candidate']
        directory = path / '.git/uif-owner-handoffs'
        handoff_sha = await asyncio.to_thread(_verify_producer_handoff, receipt, directory, candidate)
        slot = self._slot.set('owner-candidate-handoff')
        try:
            body = ('Existing OffMenu publisher handoff, reviewed candidate ' + candidate
                    + '. Private receipt at ' + str(receipt.path) + ', SHA-256 ' + handoff_sha
                    + '. Publication awaits the existing publisher under its original authority.')
            await asyncio.to_thread(self.linear, 'comment', issue.id, {'body': body})
        finally:
            self._slot.reset(slot)
        return handoff_sha

    def prepare_handoff(self, path, grant, claim, settlement, destination):
        directory = path / '.git/uif-original-effects'
        name = grant.claim(self.remote.identity).request_id + '.receiving'
        intent = {'issue_id': grant.scope.key.issue_id, 'fence': asdict(claim.lease.fence),
                  'attempt_id': claim.attempt_id, 'run': settlement['run'],
                  'scope_sha256': grant.scope_sha256, 'native_publication': destination,
                  'workspace_sha256': workspace_recovery_digest(path)}
        _preserve(directory, name, wire(intent))

    async def receive_publisher(self, owner, issue, grant, claim, path):
        directory = path / '.git/uif-original-effects'
        key = grant.claim(self.remote.identity).request_id
        pending = directory / (key + '.receiving')
        complete = directory / (key + '.received')
        if not pending.is_file() or complete.exists():
            return True
        require(pending == pending.resolve(strict=True), 'authority_pending_handoff_path')
        intent = decode(pending.read_bytes(), limit=8192)
        require(intent['fence'] == asdict(claim.lease.fence) and intent['attempt_id'] == claim.attempt_id
                and intent['scope_sha256'] == grant.scope_sha256 and intent['issue_id'] == issue.id,
                'authority_original_handoff_changed')
        authority = owner._delivery_authority(intent['native_publication'])
        require(authority and authority.get('role') == 'publish', 'authority_publisher_role_changed')
        reviewed = await owner._accepted_publication(path, issue, intent['run'], authority)
        if self.requires_publisher(issue.id, reviewed):
            handoff_sha = await self.send_handoff(path, issue, reviewed)
            outcome = self.installation()['assignments'][issue.id].get('publisher_outcomes', {}).get(handoff_sha)
            if outcome is None:
                return False
            # The locally installed publisher witness and separately
            # authenticated controller completion must bind the same original.
            # A delivered comment/file alone cannot release publication custody.
            require(outcome.keys() == {'schema', 'publisher', 'handoff_sha256', 'fence',
                    'publication_original_id', 'repository', 'base', 'publication_receipt_sha256'}
                    and outcome['schema'] == 'uif.offmenu-publisher-outcome/1'
                    and outcome['publisher'] == 'offmenu-cloud' and outcome['handoff_sha256'] == handoff_sha
                    and outcome['fence'] == asdict(claim.lease.fence)
                    and outcome['repository'] == grant.source.repository and outcome['base'] == grant.source.branch
                    and isinstance(outcome['publication_original_id'], str) and outcome['publication_original_id'],
                    'authority_publisher_outcome_binding')
            pinned_sha(outcome['publication_receipt_sha256'])
            ack = self.remote.transport.request('/v1/completion/read', {'current': asdict(claim.lease)}, allow_absent=True)
            if ack is None:
                return False
            require(ack == asdict(Completion(claim.lease.fence, True, digest(outcome))),
                    'authority_publisher_outcome_unverified')
        _retain_handoff_original(directory, complete.name, wire(intent), 8192)
        return True

    async def reconcile_originals(self, native_owner, logger):
        """Resume retained UIF originals before the owner's existing reconcile tick.

        The owner supplies its current running-issue view. This callback does not
        allocate work or replace the owner's scheduler or native phase writers.
        """
        try:
            await self.receive_pending(native_owner)
        except (OSError, Hold):
            logger.warning('Native shared handoff observation unavailable')
        try:
            await asyncio.to_thread(self.receive_late_completions, native_owner)
        except (OSError, Hold):
            logger.warning('Native shared completion observation unavailable')

    async def receive_pending(self, native_owner):
        if native_owner.project_name != 'OffMenu':
            return
        for issue_id, item in self.installation()['assignments'].items():
            if issue_id in native_owner.running or self._receiving_after.get(issue_id, 0) > time.monotonic():
                continue
            try:
                grant = self.assignment('OffMenu', issue_id, item['scope_sha256'])
                path = Path(item['workspace_binding']['path'])
                key = grant.claim(self.remote.identity).request_id
                directory = path / '.git/uif-original-effects'
                pending = directory / (key + '.receiving')
                received = pending.with_suffix('.received')
                if not pending.is_file():
                    continue
                if received.exists() and pending.with_suffix('.settled').exists():
                    continue  # Existing late-release reconciler owns this milestone.
                # This is a backoff for one already-journaled original. The
                # existing native reconciliation tick remains its sole scheduler.
                self._receiving_after[issue_id] = time.monotonic() + 30
                with _handoff_directory(directory) as fd:
                    raw = _retained_handoff_bytes(fd, pending.name, 8192)
                    try:
                        complete = _retained_handoff_bytes(fd, received.name, 8192)
                    except FileNotFoundError:
                        complete = None
                intent = decode(raw, limit=8192)
                require(raw == wire(intent) and intent['issue_id'] == issue_id
                        and intent['scope_sha256'] == grant.scope_sha256,
                        'authority_original_handoff_changed')
                current = self.remote.read_claim('OffMenu', intent['attempt_id'], True)
                require(current is not None and intent['fence'] == asdict(current.lease.fence)
                        and self.remote.original_lease(grant).fence == current.lease.fence,
                        'authority_handoff_no_longer_current')
                if complete is not None:
                    # Both records use the existing canonical intent bytes.
                    # Publication already verified; do not re-present it to
                    # the publisher or fetch a replacement review on restart.
                    require(complete == raw and workspace_recovery_digest(path) == intent['workspace_sha256'],
                            'authority_received_handoff_changed')
                    await asyncio.to_thread(self.record_settled, grant, current, path)
                    continue
                issue = await native_owner._ensure_linear_client().fetch_issue(issue_id, native_owner.cfg.tracker.project_slug)
                require(issue is not None and issue.id == issue_id, 'authority_handoff_issue_identity')
                author = item.get('review_of', {}).get(current.attempt_id)
                require(author in grant.attempts, 'authority_handoff_reviewer_identity')
                proof = self.remote.transport.request('/v1/review/read', {
                    'current': asdict(current.lease), 'author_attempt_id': author,
                    'review_attempt_id': current.attempt_id})
                candidate = proof['candidate']
                require(proof['fence'] == asdict(current.lease.fence) and digest(candidate) == proof['candidate_sha256']
                        and candidate['source'] == asdict(grant.source) and candidate['issue_id'] == issue.id
                        and candidate['scope_sha256'] == grant.scope_sha256, 'authority_original_handoff_review')
                expected = self._review_postimage(native_owner, issue, grant, candidate)
                require(self.verify_workspace(native_owner, issue, grant, review_postimage=expected) == path
                        and workspace_recovery_digest(path) == intent['workspace_sha256'],
                        'authority_original_handoff_workspace_changed')
                # The normal native publisher check also verifies current
                # Linear scope, state, human context and durable report.
                token = self._running.set((grant, current))
                try:
                    if await self.receive_publisher(native_owner, issue, grant, current, path):
                        await asyncio.to_thread(self.record_settled, grant, current, path)
                finally:
                    self._running.reset(token)
            except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError):
                continue

    def record_settled(self, grant, claim, path):
        key = grant.claim(self.remote.identity).request_id
        original = self.remote.original_lease(grant)
        require(original.fence == claim.lease.fence, 'authority_original_settlement_fence')
        _preserve(path / '.git/uif-original-effects', key + '.settled', wire(asdict(original)))
        self.try_release(grant, path)

    def try_release(self, grant, path):
        key = grant.claim(self.remote.identity).request_id
        directory = path / '.git/uif-original-effects'
        pending = directory / (key + '.settled')
        complete = directory / (key + '.released')
        if not pending.is_file() or complete.exists():
            return
        receipt = directory / (key + '.received')
        source_work = self.installation()['assignments'][grant.scope.key.issue_id].get('source_overlays')
        if (source_work or (directory / (key + '.receiving')).exists()) and not receipt.exists():
            return
        require(pending.resolve(strict=True) == pending and pending.stat().st_size <= 4096,
                'authority_retained_lease_path')
        lease = _lease(decode(pending.read_bytes(), limit=4096))
        require(lease.fence == self.remote.original_lease(grant).fence,
                'authority_release_original_fence')
        receipt = self.remote.release_verified(lease)
        if receipt is not None:
            _retain_handoff_original(directory, complete.name, wire(asdict(receipt)), 8192)

    def receive_late_completions(self, native_owner):
        if native_owner.project_name != 'OffMenu':
            return
        # Called from the existing native reconciliation tick. A controller
        # completion fact can arrive after the worker has already settled.
        # The native issue selector remains the only allocator.
        config = self.installation()
        for issue_id, item in config['assignments'].items():
            if issue_id in native_owner.running:
                continue
            try:
                grant = self.assignment('OffMenu', issue_id, item['scope_sha256'])
                candidate = Path(item['workspace_binding']['path'])
                require(candidate.parent == Path(config['workspace_root']) and candidate == candidate.resolve(),
                        'authority_completion_workspace')
                self.try_release(grant, candidate)
            except (OSError, ValueError, KeyError, RuntimeError):
                continue

    def current(self, issue_id):
        pair = self._running.get()
        require(pair is not None and pair[0].scope.key.issue_id == issue_id, 'authority_native_context')
        grant, original = pair
        require(self.assignment('OffMenu', issue_id, grant.scope_sha256) == grant,
                'authority_native_grant_changed')
        claim = self.remote.read_claim('OffMenu', original.attempt_id, True)
        require(claim is not None and claim.lease.fence == original.lease.fence
                and self.remote.clock().timestamp() < claim.lease.expires_at, 'authority_original_lease_changed')
        # Renew only the same still-live lease, under its original CAS request.
        # The receiver refuses release/replacement until earlier effects settle.
        remaining = claim.lease.expires_at - self.remote.clock().timestamp()
        lease_id = claim.lease.fence.lease_id
        if remaining < 100 and time.monotonic() >= self._renew_after.get(lease_id, 0):
            self._renew_after[lease_id] = time.monotonic() + min(15, max(0.5, remaining / 4))
            try:
                claim = self.remote.renew(claim)
            except Hold:
                # A short but valid lease can already end at its registered
                # grant's maximum. Only a fresh authenticated read gives use.
                claim = self.remote.read_claim('OffMenu', original.attempt_id, True)
                require(claim is not None and claim.lease.fence == original.lease.fence
                        and self.remote.clock().timestamp() < claim.lease.expires_at,
                        'authority_renewal_unavailable')
        return claim

    def _installed_provider_discovery(self):
        if self._provider_discovery is not None:
            return self._provider_discovery
        # Only the already installed fixed Workers AI clients have a proven
        # endpoint in this composition. No caller paths, creds, or URL plans.
        build = sha256(Path(workers_ai_client.__file__).read_bytes()).hexdigest()
        adapters = []
        for name, exact in self.remote.routes.items():
            route = ProviderRoute(
                'workers-ai-' + name.lower(), 'cloudflare', 'workers-ai', exact.model,
                build, digest({'provider': 'cloudflare', 'account_id': exact.account_id}),
                exact.credential_epoch_sha256, digest(asdict(exact)),
                self.remote.workspace_id, self.remote.project_id, 'maldous/offmenu',
                frozenset({'author'} if name == 'OffMenu' else {'review'}),
            )

            async def observe(selected=exact):
                def current():
                    receipt = self.remote.read_usage(selected)
                    require(self.remote.verify_usage(self.remote.quota_issuer, receipt) is True,
                            'authority_quota_observation_not_authenticated')
                    return receipt
                # Task cancellation cannot interrupt a running worker thread.
                # Shield its real completion so discovery quarantines this route
                # until the authenticated transport has actually returned.
                thread = asyncio.create_task(asyncio.to_thread(current))
                try:
                    return await asyncio.shield(thread)
                except asyncio.CancelledError:
                    try:
                        await asyncio.shield(thread)
                    except Exception:
                        pass
                    raise

            def project(receipt, request, fixed=route, selected=exact):
                now = self.remote.clock()
                proof = receipt.projection
                deadline = min(proof.valid_until, now + timedelta(seconds=90))
                ready = allows_request(selected, proof, now=now, request_not_after=deadline,
                    outstanding_local=0, request_maximum=MODEL_RESERVATIONS[selected.model])
                return RouteAvailability(fixed, request, 'available' if ready else 'unknown',
                    proof.covered_through.timestamp(), proof.valid_until.timestamp(),
                    receipt.receipt_sha256, ready, ready, ready)

            adapters.append(Adapter(route, observe, project))
        self._provider_discovery = NormalProviderDiscovery(tuple(adapters),
            clock=lambda: self.remote.clock().timestamp(), interval=20, timeout=8)
        return self._provider_discovery

    def _discovered_preview(self, name, timeout):
        # Scope-free candidate capacity only. The actual admitted attempt and
        # physical send each re-read authenticated, lease-bounded evidence.
        if self._provider_discovery is None:
            return None
        receipt = self._provider_discovery.observed('workers-ai-' + name.lower())
        if receipt is None:
            return None
        now = self.remote.clock()
        proof = receipt.projection
        route = self.remote.routes[name]
        deadline = now.replace(microsecond=0) + timedelta(seconds=int(timeout))
        if (not now < deadline or not allows_request(route, proof, now=now,
                request_not_after=deadline, outstanding_local=0,
                request_maximum=MODEL_RESERVATIONS[route.model])):
            return None
        return FreeAdmissionWindow(deadline, {'OffMenu': proof})

    def usage(self, timeout):
        # SharedFreePorts binds its request deadline to the current live lease
        # again at send. Never reserve a 600-second request on a 300-second lease.
        timeout = min(timeout, 90)
        pair = self._running.get()
        names = self.remote.account_ports.keys()
        if pair:
            claim = self.current(pair[0].scope.key.issue_id)
            names = [self.attempt_account(pair[0], claim.attempt_id)]
            timeout = min(timeout, claim.lease.expires_at - int(self.remote.clock().timestamp()) - 1)
        windows = {}
        for name in names:
            try:
                if pair is None and self._provider_discovery is not None and self._provider_discovery.active:
                    preview = self._discovered_preview(name, timeout)
                    if preview is not None:
                        windows[name] = preview
                else:
                    windows[name] = self.remote.account_ports[name].read_account_usage(timeout)
            except BudgetRefused:
                continue
        if not windows:
            raise BudgetRefused('No independently verified account evidence for this attempt')
        return FreeAdmissionWindow(min(w.request_not_after for w in windows.values()),
            {name: w.usage_snapshots['OffMenu'] for name, w in windows.items()})

    def attempt_account(self, grant, attempt):
        reviewed = self.installation()['assignments'][grant.scope.key.issue_id].get('review_of', {})
        if attempt in reviewed:
            require('UIF' in self.remote.routes and reviewed[attempt] in grant.attempts
                    and reviewed[attempt] != attempt, 'authority_distinct_reviewer_unavailable')
            return 'UIF'
        return 'OffMenu'

    def open_fenced(self, call):
        pair = self._running.get()
        require(pair is not None and call.attempt_id == pair[1].attempt_id, 'authority_current_attempt')
        name = self.attempt_account(pair[0], call.attempt_id)
        return self.remote.account_ports[name].open_fenced_connection(call)


    def options(self):
        return dict(self.remote.ports.launcher_options(), read_account_usage=self.usage,
                    open_fenced_connection=self.open_fenced, authority_lifecycle=self,
                    provider_discovery=self._installed_provider_discovery())

    def _waiting_gate_body(self, claim, issue_id, original_id, body):
        """Reuse only the clock field of a byte-identical native gate comment."""
        prefix, _, human = body.partition('\n\n')
        match = re.fullmatch(r'<!-- stokowski:gate (.{1,4096}) -->', prefix)
        require(match is not None and bool(human), 'authority_waiting_gate_marker')
        marker = decode(match[1].encode(), limit=8192)
        if marker.get('status') != 'waiting':
            return body  # Other native effects keep exact-byte behavior.
        require(set(marker) == {'state', 'status', 'run', 'timestamp'}
                and type(marker['state']) is str and bool(marker['state'])
                and type(marker['run']) is int and marker['run'] > 0,
                'authority_waiting_gate_marker')
        stamp = marker['timestamp']
        pattern = r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{6})?\+00:00'
        require(type(stamp) is str and re.fullmatch(pattern, stamp) is not None,
                'authority_waiting_gate_clock')
        datetime.fromisoformat(stamp)
        field = f'"timestamp": "{stamp}"'
        require(prefix.count(field) == 1, 'authority_waiting_gate_marker')
        semantic = prefix.replace(field, '"timestamp": null', 1) + '\n\n' + human
        item = self.installation()['assignments'][issue_id]
        directory = Path(item['workspace_binding']['path']) / '.git/uif-original-effects'
        filename = original_id + '.gate-clock'
        directory.mkdir(mode=0o700, exist_ok=True)
        require(directory == directory.resolve(strict=True), 'authority_private_receipt_directory')
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            require(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700,
                    'authority_private_receipt_directory')
            identity = {'fence': asdict(claim.lease.fence), 'original_id': original_id,
                        'semantic_sha256': sha256(semantic.encode()).hexdigest()}
            try:
                raw = _read_gate_clock(fd, filename)
            except FileNotFoundError:
                # Existing originals retain exact-byte semantics. Never infer
                # their original clock or migrate them to a new effect.
                try:
                    os.stat(original_id + '.linear', dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    return body
                try:
                    _publish_gate_clock(fd, filename, wire(dict(identity, timestamp=stamp)))
                except FileExistsError:
                    pass  # Reopen the winner; the semantic/fence checks below still apply.
                raw = _read_gate_clock(fd, filename)
                os.fsync(fd)
            linked = directory.lstat()
            require((linked.st_dev, linked.st_ino, linked.st_mode, linked.st_uid) ==
                    (info.st_dev, info.st_ino, info.st_mode, info.st_uid),
                    'authority_private_receipt_directory_changed')
        finally:
            os.close(fd)
        saved = decode(raw, limit=8192)
        prior = saved.get('timestamp')
        require(type(prior) is str and re.fullmatch(pattern, prior) is not None
                and saved == dict(identity, timestamp=prior)
                and raw == wire(saved), 'authority_waiting_gate_semantics_changed')
        datetime.fromisoformat(prior)
        return prefix.replace(field, f'"timestamp": "{prior}"', 1) + '\n\n' + human

    def retained_linear(self, operation):
        """Keep the original fingerprint before a possibly partial network effect."""
        issue_id = operation.fence.scope.key.issue_id
        item = self.installation()['assignments'][issue_id]
        directory = Path(item['workspace_binding']['path']) / '.git/uif-original-effects'
        destination = directory / (operation.original_id + '.linear')
        if not destination.exists():
            _preserve(directory, destination.name, wire(asdict(operation)))
        require(destination.resolve(strict=True) == destination and destination.stat().st_size <= 8192,
                'authority_linear_original_path')
        saved = decode(destination.read_bytes(), limit=8192)
        require(saved['fence'] == asdict(operation.fence) and saved['original_id'] == operation.original_id,
                'authority_linear_original_fence_changed')
        original = Operation(operation.fence, operation.original_id, saved['payload_sha256'])
        if original != operation:
            prior = self.remote.transport.request('/v1/linear/receipt', {'operation': asdict(original)},
                                                  allow_absent=True)
            if prior is not None:
                self._verified_terminal(original, prior)
                raise Hold('authority_previous_linear_effect_verified_different_original_payload')
            raise Hold('authority_previous_linear_send_unverified_different_original_payload')

    def linear(self, kind, issue_id, payload):
        claim = self.current(issue_id)
        slot = self._slot.get()
        require(slot is not None, 'authority_original_linear_slot_missing')
        original_id = digest({'attempt': claim.attempt_id, 'slot': slot, 'kind': kind, 'issue': issue_id})
        body = payload.get('body')
        if (kind == 'comment' and slot == '_enter_gate' and type(body) is str
                and body.startswith('<!-- stokowski:gate ')):
            payload = dict(payload, body=self._waiting_gate_body(claim, issue_id, original_id, body))
            # A stale worker cannot carry clock retention into a new fence.
            current = self.current(issue_id)
            require(current.lease.fence == claim.lease.fence and current.attempt_id == claim.attempt_id,
                    'authority_waiting_gate_fence_changed')
        args = {'attempt_id': claim.attempt_id, 'issue_id': issue_id, **payload}
        sha = digest({'kind': 'uif.linear-' + kind + '/1', **args})
        # Different semantic bytes for the same native stage and effect slot
        # remain a conflict. A clock-only retry recovers the exact first bytes.
        op = Operation(claim.lease.fence, original_id, sha)
        self.retained_linear(op)
        result = self.remote.transport.request('/v1/linear/receipt', {'operation': asdict(op)}, allow_absent=True)
        if result is None:
            result = self.remote.transport.request('/v1/linear/' + kind,
                        {'operation': asdict(op), 'attempt_id': claim.attempt_id, **payload})
        require(kind == 'comment' and str(UUID(result['id'])) == result['id'], 'authority_linear_comment_result')
        self._verified_terminal(op, result)
        return True

    def _verified_terminal(self, op, result):
        observed = self.remote.read_original(op)
        require(observed.state == 'terminal' and observed.terminal.status == 'verified'
                and observed.terminal.receipt_sha256 == result['receipt_sha256'],
                'authority_linear_effect_unsettled')

    def upload(self, filename, content_type, data):
        pair = self._running.get()
        require(pair is not None and self._slot.get() == '_publish_run_report'
                and type(data) is bytes and 0 < len(data) <= 8 * 1024 * 1024,
                'authority_stage_artifact_scope')
        issue_id = pair[0].scope.key.issue_id
        claim = self.current(issue_id)
        body_sha = sha256(data).hexdigest()
        args = {'attempt_id': claim.attempt_id, 'filename': filename, 'content_type': content_type}
        op = Operation(claim.lease.fence, digest({'attempt': claim.attempt_id,
                       'slot': 'retained-artifact', 'issue': issue_id, 'filename': filename}),
                       digest({'kind': 'uif.linear-upload/1', 'issue_id': issue_id, **args,
                               'size': len(data), 'body_sha256': body_sha}))
        self.retained_linear(op)
        result = self.remote.transport.request('/v1/linear/receipt', {'operation': asdict(op)}, allow_absent=True)
        if result is None:
            result = self.remote.transport.request('/v1/linear/upload', {**args, 'operation': asdict(op),
                                              'body_base64': b64encode(data).decode('ascii')}, timeout=60)
        asset = urlsplit(result['asset_url'])
        require(asset.scheme == 'https' and asset.netloc == 'uploads.linear.app'
                and asset.path.startswith('/') and not asset.fragment and not asset.query,
                'authority_artifact_destination')
        self._verified_terminal(op, result)
        return result['asset_url']


class _LinearClient:
    def __init__(self, reader, composition):
        self._reader, self._composition = reader, composition

    def __getattr__(self, name):
        if name in _LINEAR_READS:
            return getattr(self._reader, name)
        raise AttributeError(name)

    async def post_comment(self, issue_id, body):
        return await asyncio.to_thread(self._composition.linear, 'comment', issue_id, {'body': body})

    async def update_issue_state(self, issue_id, state_name):
        # The installed Linear issueUpdate has no atomic match-on-current-state predicate.
        return False

    async def add_label(self, issue_id, label_id):
        raise Hold('authority_linear_label_receiver_unavailable')

    async def create_label(self, team_id, name, color=None):
        raise Hold('authority_linear_label_receiver_unavailable')

    async def upload_file(self, filename, content_type, data):
        return await asyncio.to_thread(self._composition.upload, filename, content_type, data)


def load_guarded_startup(path, unit, stage):
    """Select normal startup or the explicitly installed, status-only hold."""
    data = protected_record(path)
    if data.get('schema') != 'uif.stokowski-remote-idle/1':
        composition = load_composition(path)
        return composition, composition.startup_arguments()

    require(data.keys() == {'schema', 'unit', 'stage', 'origin', 'access_credential',
                            'native_identity', 'coordinator_epoch', 'hold_reason'}
            and (data['unit'], data['stage']) == (unit, stage)
            and type(data['coordinator_epoch']) is int and data['coordinator_epoch'] > 0
            and data['hold_reason'] in ('free_route_unavailable', 'free_quota_unavailable'),
            'authority_idle_installation')
    identity = AuthorityIdentity(**data['native_identity'])
    transport = AccessTransport(data['origin'], data['access_credential'])
    binding = (unit, stage, identity.authority_id, identity.session_id, data['coordinator_epoch'],
               identity.deployment_sha256, identity.policy_sha256)

    def verifier(request):
        schema, wanted, deadline = request
        require(schema == 'uif.stokowski.native-start/1' and wanted == binding
                and time.monotonic_ns() < deadline
                and protected_record(path) == data, 'authority_idle_challenge')
        status = transport.request('/v1/status', timeout=4)
        require(all(status.get(k) == v for k, v in asdict(identity).items())
                and status.get('role') == 'native' and status.get('stopped') is True
                and type(status.get('coordinator_epoch')) is int
                and status['coordinator_epoch'] == data['coordinator_epoch']
                and protected_record(path) == data and time.monotonic_ns() < deadline,
                'authority_idle_status_binding')
        return request, binding, 'stopped_idle', deadline

    return None, dict(startup_binding=binding, startup_verifier=verifier,
                      idle_reason=data['hold_reason'])


def load_composition(path):
    data = protected_record(path)
    identity = AuthorityIdentity(**data['native_identity'])
    issuer = AuthorityIdentity(**data['quota_issuer'])
    remote = RemoteAuthority(AccessTransport(data['origin'], data['access_credential']),
                             native=identity, quota_issuer=issuer, route=FreeRoute(**data['route']),
                             repository_id=data['repository_id'], workspace_id=data['workspace_id'],
                             project_id=data['project_id'],
                             reviewer_route=FreeRoute(**data['reviewer_route']) if data.get('reviewer_route') else None)
    require(remote.route.model == GLM_MODEL, 'authority_free_model')
    value = NativeComposition(remote, path)
    value.installation()
    return value

# Each slot is a native semantic operation. The receiving authority keeps a
# single original across retries even if a fresh Linear marker has a new time.
_COMMENT_SLOTS = (
    '_enter_gate', '_transition', '_announce_state', '_hold_prelaunch_input',
    '_settle_contribution_receiving', '_settle_checkpoint_receiving', '_refresh_management_holds',
    '_publication_preflight', '_publish_run_report',
)


def install_sinks(orchestration, composition):
    """Install before native start, on the reviewed pinned classes only."""
    original_client = orchestration.LinearClient
    require(not getattr(original_client, '_shared_receiver', False), 'authority_composition_already_installed')
    owner = orchestration.Orchestrator
    original_reconcile = owner._reconcile
    original_settle = owner._resume_settlement
    original_preflight = owner._publication_preflight
    slots = {name: getattr(owner, name) for name in _COMMENT_SLOTS}
    require(all(asyncio.iscoroutinefunction(fn) for fn in slots.values()), 'authority_unrecognized_native_sink')

    def client(*args, **kwargs):
        return _LinearClient(original_client(*args, **kwargs), composition)
    client._shared_receiver = True

    def wrap(name, native):
        @wraps(native)
        async def with_original(*args, **kwargs):
            slot = composition._slot.set(name)
            try:
                return await native(*args, **kwargs)
            finally:
                composition._slot.reset(slot)
        return with_original

    @wraps(original_settle)
    async def settle(native_owner, issue, attempt):
        try:
            return await settle_bound(native_owner, issue, attempt)
        except (Hold, OSError, KeyError, ValueError) as error:
            attempt.error = 'Original shared settlement awaiting reconciliation: ' + type(error).__name__
            orchestration.logger.warning('Original shared settlement held for %s (%s)', issue.identifier, type(error).__name__)
            return False

    async def settle_bound(native_owner, issue, attempt):
        with composition.settlement_context(native_owner, issue, attempt) as (grant, original):
            result = await original_settle(native_owner, issue, attempt)
            if result is not True or attempt.settlement_record.get('phase') != 'complete':
                return result

            # Preserve the completed original even if its new receiving or
            # release step is unavailable. Reporting this as an unsettled
            # worker would invite the old native callback to replay it.
            path = native_owner._settlement_path(issue)
            try:
                if attempt.state_name == 'independent-review':
                    target = native_owner._states_for(issue).get(attempt.settlement_record.get('target'))
                    destination = getattr(target, 'publication', None)
                    authority = native_owner._delivery_authority(destination) if destination else None
                    if authority and authority.get('role') == 'publish':
                        composition.prepare_handoff(path, grant, original, attempt.settlement_record, destination)
                        if not await composition.receive_publisher(native_owner, issue, grant, original, path):
                            return result
                composition._private_postimages[issue.id] = await asyncio.to_thread(
                    workspace_recovery_digest, path)
                await asyncio.to_thread(composition.record_settled, grant, original, path)
            except (OSError, KeyError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                # The retained native candidate and the original operation stay
                # available to the normal publisher/release reconciliation.
                # Never mask the original outcome or invent a successor.
                orchestration.logger.warning(
                    'Native settlement complete for %s; receiving pending (%s)',
                    issue.identifier, type(error).__name__)
            return result

    @wraps(original_preflight)
    async def preflight(native_owner, path, issue, run, authority):
        selected = await native_owner._accepted_publication(path, issue, run, authority)
        if composition.requires_publisher(issue.id, selected):
            # A publish-stage attempt does not acquire the original review
            # attempt or workspace. Its accepted owner handoff remains bound
            # to that original review, even after another stage is admitted.
            raise Hold('Reviewed source candidate awaits the existing sole publisher')
        require(selected['binding']['kind'] == 'source-free', 'authority_unrecognized_publication_kind')
        return await original_preflight(native_owner, path, issue, run, authority)

    @wraps(original_reconcile)
    async def reconcile(native_owner, *args, **kwargs):
        await composition.reconcile_originals(native_owner, orchestration.logger)
        return await original_reconcile(native_owner, *args, **kwargs)

    owner._reconcile = reconcile
    slots['_publication_preflight'] = preflight
    owner._resume_settlement = settle
    orchestration.LinearClient = client
    for name, original in slots.items():
        setattr(owner, name, wrap(name, original))
