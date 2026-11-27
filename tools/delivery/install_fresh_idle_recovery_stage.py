"""Stage fixed recovery package only; never modifies the existing unit or starts it."""

from dataclasses import dataclass
from pathlib import Path
from tempfile import mkdtemp

import guarded_service_upgrade as guard
from install_guarded_idle_bootstrap import _publish_once, _rename_noreplace, _sync_parent

INPUT = guard.ROOT / 'idle-recovery-input-v1'
STAGE = guard.ROOT / 'idle-recovery-v1'
MEMBERS = ('fresh_idle_generation.py', 'fresh_idle_generation_controller.py',
           'fresh_idle_recovery_host.py', 'fresh_idle_recovery_receiver.py',
           '90-uif-fresh-idle-recovery.conf')


@dataclass(frozen=True)
class RecoveryStageApproval:
    operation_id: str
    prior_dropin_sha256: str
    retained_selector_sha256: str
    member_sha256: tuple


def _same_or_create(path, raw):
    try:
        current = guard.read_record(path.parent, path.name)
    except FileNotFoundError:
        _publish_once(path, 0o400, lambda out: out.write(raw))
        current = guard.read_record(path.parent, path.name)
    guard.require(current == raw, 'different bytes at reserved recovery leaf')


def stage(approved):
    """Trusted fixed maintenance carrier alone supplies the immutable approval."""
    guard.require(type(approved) is RecoveryStageApproval and guard._private_webhook_root()
                  and type(approved.member_sha256) is tuple
                  and len(approved.member_sha256) == len(MEMBERS)
                  and all(guard.hash_pin(v) for v in (approved.operation_id,
                          approved.prior_dropin_sha256, approved.retained_selector_sha256,
                          *approved.member_sha256)), 'expected protected exact recovery approval')
    with guard.selector_lock(shared=False):
        def preimage():
            guard.require(guard.bytes_pin(guard.DROPIN) == approved.prior_dropin_sha256
                          and guard.bytes_pin(guard.SELECTOR) == approved.retained_selector_sha256,
                          'installed recovery preimage differs')
        preimage()
        payload = {}
        for name, pin in zip(MEMBERS, approved.member_sha256):
            raw = guard.read_record(INPUT, name)
            guard.require(guard.digest(raw) == pin, 'protected recovery member mismatch')
            payload[name] = raw
        intent = guard.encode({'schema': 'uif.foo-selected-idle-recovery-stage/1',
            'operation_id': approved.operation_id, 'prior_dropin_sha256': approved.prior_dropin_sha256,
            'retained_selector_sha256': approved.retained_selector_sha256,
            'members': dict(zip(MEMBERS, approved.member_sha256))})
        receipt = guard.encode({'schema': 'uif.foo-selected-idle-recovery-staged/1',
            'operation_id': approved.operation_id, 'intent_sha256': guard.digest(intent)})
        expected = {**payload, 'intent.json': intent, 'receipt.json': receipt}

        def verify_complete(directory):
            guard.require({p.name for p in directory.iterdir()} == set(expected)
                          and all(guard.read_record(directory, name) == raw
                                  for name, raw in expected.items()),
                          'published recovery closure differs from same original')
            preimage()

        if STAGE.exists() or STAGE.is_symlink():
            verify_complete(STAGE)
        else:
            guard.protected_parents(STAGE)
            temporary = Path(mkdtemp(prefix='.selected-idle-stage-', dir=STAGE.parent))
            try:
                for name, raw in expected.items():
                    _same_or_create(temporary / name, raw)
                _sync_parent(temporary / 'receipt.json')
                verify_complete(temporary)
                try:
                    _rename_noreplace(temporary, STAGE)
                except OSError:
                    # If the publish outcome is uncertain, only accept the
                    # original if all exact final bytes are independently there.
                    if not STAGE.exists() and not STAGE.is_symlink():
                        raise
                    verify_complete(STAGE)
                    _sync_parent(STAGE)
                verify_complete(STAGE)
            finally:
                # On successful rename, the old name no longer exists.
                if temporary.exists():
                    for name in expected:
                        (temporary / name).unlink(missing_ok=True)
                    temporary.rmdir()
        return {'status': 'staged_not_activated', 'intent_sha256': guard.digest(intent),
                'receipt_sha256': guard.digest(receipt)}
