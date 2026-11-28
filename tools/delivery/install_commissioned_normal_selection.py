"""Fixed private filesystem adapter for the first guarded-to-normal transition.

Not a CLI or an admission issuer. The authenticated maintenance controller must
supply its own trusted resolver of an independently approved existing original.
Do not bind this function to request fields or generic maintenance shell input.
"""
from contextlib import contextmanager
from pathlib import Path
import os
import re
import stat

import commissioned_normal_service as normal
import commit_commissioned_normal_selection as transaction
import guarded_service_upgrade as guard
import observe_guarded_normal_preimage as guarded
import read_normal_effective_state as effective
import stage_commissioned_normal_runtime as staging
from install_guarded_idle_bootstrap import _publish_once, _sync_parent


_ROOT_UID = 0


def _snapshot(path, modes):
    guard.protected_parents(path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        guard.require(stat.S_ISREG(before.st_mode) and before.st_uid == _ROOT_UID
                      and before.st_nlink == 1 and stat.S_IMODE(before.st_mode) in modes
                      and 0 < before.st_size <= guard.MAX_RECORD,
                      'fixed protected leaf custody or limits differ')
        raw = stream.read(guard.MAX_RECORD + 1)
        guard.require(len(raw) == before.st_size
                      and staging._identity(before) == staging._identity(os.fstat(stream.fileno()))
                      == staging._identity(path.lstat()), 'fixed protected leaf changed during read')
        return raw


class _FixedOwner:
    def __init__(self, original, archive):
        self.original = original
        self.members = staging._members(archive, original.staged)
        self.stream = None
        self.selector = Path(normal.SELECTION)
        self.dropin = guard.DROPIN.with_name(normal.DROPIN)

    def _locked(self):
        guard.require(self.stream is not None
                      and guard.lock_identity(os.fstat(self.stream.fileno())) ==
                          guard.lock_identity(guard.LOCK.lstat()), 'fixed selector lock custody changed')

    @contextmanager
    def selector_lock(self):
        with guard.selector_lock(shared=False) as stream:
            self.stream = stream
            try:
                self._locked()
                yield
                self._locked()
            finally:
                self.stream = None

    def _read(self, path, modes):
        self._locked()
        result = _snapshot(path, modes)
        self._locked()
        return result

    def guarded_dropin(self):
        return self._read(guard.DROPIN, (0o444, 0o644))

    def read_guarded_selector(self):
        return self._read(guard.SELECTOR, (0o444, 0o644))

    def read_selector(self):
        return self._read(self.selector, (0o444,))

    def read_normal_dropin(self):
        return self._read(self.dropin, (0o444,))

    def _publish_absent(self, path, prior, raw, mode):
        # This adapter handles only first publication, never replacement.
        guard.require(prior is None and self._read(path, (mode,)) is None,
                      'fixed leaf is not an absent first-install preimage')
        _publish_once(path, mode, lambda out: out.write(raw))
        self._locked()

    def publish_selector(self, prior, raw):
        self._publish_absent(self.selector, prior, raw, 0o444)

    def publish_normal_dropin(self, prior, raw):
        self._publish_absent(self.dropin, prior, raw, 0o444)

    def _journal(self, operation, suffix):
        guard.require(guard.hash_pin(operation) and operation == self.original.operation_id,
                      'unexpected fixed journal original')
        return guard.RECEIPTS / (operation + suffix)

    def read_intent(self, operation):
        return self._read(self._journal(operation, '.normal-select-intent.json'), (0o400,))

    def persist_intent_once(self, operation, raw):
        self._publish_absent(self._journal(operation, '.normal-select-intent.json'), None, raw, 0o400)

    def sync_intent_parent(self, operation):
        self._sync(self._journal(operation, '.normal-select-intent.json'))

    def read_disk_receipt(self, operation):
        return self._read(self._journal(operation, '.normal-select-committed.json'), (0o400,))

    def persist_disk_receipt_once(self, operation, raw):
        self._publish_absent(self._journal(operation, '.normal-select-committed.json'), None, raw, 0o400)

    def sync_disk_receipt_parent(self, operation):
        self._sync(self._journal(operation, '.normal-select-committed.json'))

    def _sync(self, path):
        self._locked()
        guard.protected_parents(path)
        _sync_parent(path)
        self._locked()

    def sync_selector_parent(self):
        self._sync(self.selector)

    def sync_target_parents(self):
        self.sync_selector_parent()
        self._sync(self.dropin)

    def verify_archive_target(self, selected):
        stage = guard.STAGES / self.original.staged.stage_leaf
        guard.require(selected['stage'] == str(stage)
                      and all(selected[key] == guard.digest(self.members[name][0]) for key, name in (
                          ('launcher_sha256', 'launch.py'), ('interpreter_sha256', guard.INTERPRETER),
                          ('authority_config_sha256', guard.IDLE_CONFIG))),
                      'normal selector pins differ from accepted archive bytes')
        return stage

    def verify_staged(self, receipt, selected):
        self._locked()
        stage = self.verify_archive_target(selected)
        staging._verify_directory(stage, {**self.members, staging.RECEIPT: (receipt, 0o400)})
        self._locked()
        return True

    def verify_guarded_effective(self, prior):
        self._locked()
        return guarded.observe_guarded_preimage(self.stream, prior, conditions=prior.conditions,
                                               asserts=prior.asserts)

    def observe_effective_normal(self, original):
        self._locked()
        return effective.observe_normal_effective(original)


def _resolve_exact_owner_original(resolve_protected_original):
    guard.require(guard._private_webhook_root(), 'requires existing root maintenance owner context')
    original, archive, selector, dropin = resolve_protected_original()
    guard.require(type(original) is transaction.SelectionOriginal
                  and type(original.staged) is staging.NormalRuntimeStageApproval
                  and original.prior_selector_sha256 is None
                  and original.prior_normal_dropin_sha256 is None
                  and type(original.staged.stage_leaf) is str
                  and re.fullmatch(r'uif1313-normal-[a-z0-9][a-z0-9.-]{1,65}', original.staged.stage_leaf), 'not a first fixed normal selection original')
    return original, archive, selector, dropin


def _install_from_trusted_owner(resolve_protected_original):
    """Private composition seam; resolver must be supplied by protected code.

    Resolves the exact normal transaction original, matching archive, rendered
    bytes, and independently retained typed guarded manager pins. Root transport
    alone is not that resolver. No host binding is installed by this module.
    """
    original, archive, selector, dropin = _resolve_exact_owner_original(resolve_protected_original)
    return transaction.commit_normal_selection(
        original, selector, dropin, _FixedOwner(original, archive))


_MANAGER_RESULTS = frozenset({
    'disk_release_differs', 'manager_observation_changed', 'manager_readback_unavailable',
    'disk_matches_manager_reload_needed', 'disk_matches_effective_manager_differs',
    'effective_normal_not_stopped', 'effective_normal_start_history_changed',
    'effective_normal_stopped_same_original_observation',
})


def _stage_and_select_from_trusted_owner(resolve_protected_original):
    """Owner-bound fixed install action. No arguments from a remote request.

    Both existing stages retain their own immutable same-original receipts.
    A crash between them leaves an exact inspectable stage, not a live start.
    The selection step rechecks the full protected stage and guarded preimage.
    The authenticated owner resolver and any later manager action are external.
    """
    original, archive, selector, dropin = _resolve_exact_owner_original(resolve_protected_original)
    selected = transaction._validated_target(original, selector, dropin)
    owner = _FixedOwner(original, archive)
    owner.verify_archive_target(selected)
    staged = staging.stage_normal_runtime(original.staged, archive)
    committed = transaction.commit_normal_selection(original, selector, dropin, owner)
    manager = committed['manager']['status']
    guard.require(manager in _MANAGER_RESULTS
                  and staged['status'] == 'normal_stage_materialized_not_selected'
                  and staged['release_sha256'] == original.staged.members_sha256
                  and committed['status'] == 'normal_pair_committed_on_disk'
                  and all(guard.hash_pin(x) for x in
                          (staged['receipt_sha256'], committed['receipt_sha256'])),
                  'unexpected normal installer receipt')
    return {'status': 'normal_pair_committed_on_disk', 'operation_id': original.operation_id,
            'release_sha256': staged['release_sha256'], 'staging_receipt_sha256': staged['receipt_sha256'],
            'selection_receipt_sha256': committed['receipt_sha256'], 'manager_status': manager,
            'installation_accepted': False, 'native_startup_verified': False}
