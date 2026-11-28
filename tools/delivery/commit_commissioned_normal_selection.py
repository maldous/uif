"""Recoverable first normal selection, behind an already authorized owner adapter.

The adapter owns the fixed leaves, selector lock, exact protected #171 stage
readback, and same-filesystem durable compare/publish and fixed parent sync. It supplies the existing
typed manager observation only. There is no CLI, root binding, reload or start.
"""
from dataclasses import dataclass, asdict, fields

import commissioned_normal_service as normal
import guarded_service_upgrade as guard
import read_normal_effective_state as effective
from stage_commissioned_normal_runtime import NormalRuntimeStageApproval


@dataclass(frozen=True)
class SelectionOriginal:
    operation_id: str
    staged: NormalRuntimeStageApproval
    prior_selector_sha256: str | None
    prior_normal_dropin_sha256: str | None
    prior_guarded_dropin_sha256: str
    approved_normal_selector_sha256: str
    approved_normal_dropin_sha256: str
    effective_original: effective.NormalEffectiveOriginal
    prior_guarded_conditions: tuple = ()
    prior_guarded_asserts: tuple = ()


@dataclass(frozen=True)
class GuardedEffectivePrior:
    """Exact old effective binding the existing owner must independently verify.

    The owner port compares the guarded command, conditions and manager state,
    same start history, stopped/zero PID, no pending job, and no *actionable*
    start authorization under the existing guard. No marker identity is
    part of this selection original.
    """
    unit: str
    dropin_sha256: str
    selector_sha256: str
    before_start_monotonic: int
    before_restarts: int
    conditions: tuple = ()
    asserts: tuple = ()


def encode_selection_original(original):
    """Canonical bytes of the existing original wire format, not authority."""
    guard.require(type(original) is SelectionOriginal, 'expected typed normal selection original')
    return guard.encode({'schema': 'uif.foo-normal-selection-original/2', 'original': asdict(original)})


def decode_selection_original(raw, expected_sha256):
    """Restore exact bytes already pinned by the protected installation owner.

    This does not find a pin, establish the owner, read paths or install. The
    protected caller supplies the independently retained pin and bounded bytes.
    """
    guard.require(type(raw) is bytes and 0 < len(raw) <= guard.MAX_RECORD
                  and guard.hash_pin(expected_sha256) and guard.digest(raw) == expected_sha256,
                  'retained normal selection original byte pin differs')
    wire = guard.record(raw, ('schema', 'original'))
    guard.require(wire['schema'] == 'uif.foo-normal-selection-original/2',
                  'unsupported retained normal selection original schema')

    def values(node, cls):
        guard.require(type(node) is dict and node.keys() == {field.name for field in fields(cls)},
                      'unexpected retained normal selection fields')
        return node.copy()

    def predicates(rows):
        guard.require(type(rows) is list and len(rows) <= 64 and all(type(row) is list for row in rows),
                      'unexpected retained systemd predicate list')
        projected = tuple(map(tuple, rows))
        guard.require(effective.valid_predicate_pins(projected), 'unexpected retained systemd predicate shape')
        return projected

    top = values(wire['original'], SelectionOriginal)
    top['staged'] = NormalRuntimeStageApproval(**values(top['staged'], NormalRuntimeStageApproval))
    manager = values(top['effective_original'], effective.NormalEffectiveOriginal)
    for key in ('conditions', 'asserts'):
        manager[key] = predicates(manager[key])
    top['effective_original'] = effective.NormalEffectiveOriginal(**manager)
    for key in ('prior_guarded_conditions', 'prior_guarded_asserts'):
        top[key] = predicates(top[key])
    result = SelectionOriginal(**top)
    guard.require(encode_selection_original(result) == raw,
                  'retained normal selection original not canonical')
    return result


def _pin(raw):
    guard.require(raw is None or type(raw) is bytes, 'unexpected protected byte observation')
    return None if raw is None else guard.digest(raw)


def _validated_target(original, selector, dropin):
    """Shared exact original/target validation without any host effects."""
    guard.require(type(original) is SelectionOriginal
                  and type(original.staged) is NormalRuntimeStageApproval
                  and all(guard.hash_pin(value) for value in
                          (original.operation_id, original.staged.operation_id,
                           original.approved_normal_selector_sha256,
                           original.approved_normal_dropin_sha256))
                  # This is explicitly first conversion from existing guarded
                  # idle, not selection replacement for an already-normal unit.
                  and original.prior_normal_dropin_sha256 is None
                  and (original.prior_selector_sha256 is None
                       or guard.hash_pin(original.prior_selector_sha256))
                  and original.prior_guarded_dropin_sha256 == guard.digest(guard.selector_dropin())
                  and original.staged.prior_dropin_sha256 == original.prior_guarded_dropin_sha256
                  and guard.hash_pin(original.staged.prior_selection_sha256)
                  and effective.valid_predicate_pins(original.prior_guarded_conditions)
                  and effective.valid_predicate_pins(original.prior_guarded_asserts),
                  'normal selection first-transition source or authority pins changed')
    effective.validate_observation_original(original.effective_original)
    stage = normal.check_normal_stage(original.effective_original.stage)
    guard.require(stage == str(guard.STAGES / original.staged.stage_leaf)
                  and _pin(selector) == original.approved_normal_selector_sha256
                  and _pin(dropin) == original.approved_normal_dropin_sha256
                  and dropin == normal.render_normal_dropin(stage)
                  and original.effective_original.expected_dropin_sha256 ==
                      original.approved_normal_dropin_sha256
                  and original.effective_original.observed_disk_dropin_sha256 ==
                      original.approved_normal_dropin_sha256,
                  'normal selection target bytes changed')
    fields = ('schema', 'mode', 'unit', 'stage', 'source_head', 'source_tree', 'release_sha256',
              'launcher_sha256', 'interpreter_sha256', 'authority_config_sha256')
    selected = guard.record(selector, fields)
    guard.require(selector == normal.render_normal_selection(stage, **{x: selected[x] for x in fields[4:]})
                  and selected['release_sha256'] == original.staged.members_sha256,
                  'normal selector not bound to staged exact release')
    return selected


def commit_normal_selection(original, selector, dropin, owner):
    """Replay only the same journaled original; never repairs unknown disk state."""
    selected = _validated_target(original, selector, dropin)
    staging = guard.encode({'schema': 'uif.foo-normal-runtime-staged/1', **vars(original.staged)})
    intent = encode_selection_original(original)
    receipt = guard.encode({'schema': 'uif.foo-normal-selection-disk-commit/1',
                            'operation_id': original.operation_id,
                            'intent_sha256': guard.digest(intent)})
    prior = (original.prior_selector_sha256, original.prior_normal_dropin_sha256)
    target = (_pin(selector), _pin(dropin))
    intermediate = (target[0], prior[1])

    guarded_prior = GuardedEffectivePrior(
        normal.UNIT, original.prior_guarded_dropin_sha256, original.staged.prior_selection_sha256,
        original.effective_original.before_start_monotonic,
        original.effective_original.before_restarts,
        original.prior_guarded_conditions, original.prior_guarded_asserts)

    with owner.selector_lock():
        def verify_anchors():
            guard.require(_pin(owner.guarded_dropin()) == guarded_prior.dropin_sha256
                          and _pin(owner.read_guarded_selector()) == guarded_prior.selector_sha256
                          and owner.verify_staged(staging, selected) is True,
                          'original guarded profile or accepted normal stage changed')

        def pair():
            return _pin(owner.read_selector()), _pin(owner.read_normal_dropin())

        def verify_intermediate():
            verify_anchors()
            # Existing reviewed legacy loader checks its own guard.SELECTOR,
            # not this distinct normal selector. Owner proves that unchanged
            # old executable/profile remains effective before publishing.
            guard.require(owner.verify_guarded_effective(guarded_prior) is True,
                          'legacy effective preimage not verified')

        verify_anchors()
        observed = pair()
        prior_intent = owner.read_intent(original.operation_id)
        if prior_intent is None:
            guard.require(observed == prior, 'no original for already changed normal pair')
            verify_intermediate()
            owner.persist_intent_once(original.operation_id, intent)
        guard.require(owner.read_intent(original.operation_id) == intent,
                      'retained normal selection original differs')
        # Exact visible intent may originate from an interrupted publication
        # after rename and before parent fsync. Make it durable before effects.
        owner.sync_intent_parent(original.operation_id)
        guard.require(owner.read_intent(original.operation_id) == intent,
                      'retained normal selection original changed during sync')
        if observed == prior and prior[0] != target[0]:
            verify_intermediate()
            guard.require(pair() == prior, 'normal prior pair changed')
            owner.publish_selector(prior[0], selector)
            observed = pair()
        if observed == intermediate:
            # A retained selector can be visible after its rename but before
            # the directory sync. Durably order that selector before touching
            # the drop-in (which may have a different parent).
            owner.sync_selector_parent()
            verify_intermediate()
            guard.require(pair() == intermediate, 'normal intermediate pair changed')
            owner.publish_normal_dropin(prior[1], dropin)
            observed = pair()
        guard.require(observed == target and pair() == target, 'normal selection mixed or foreign state')
        verify_anchors()
        # A lost directory-sync acknowledgement may leave the pair visible.
        owner.sync_target_parents()
        guard.require(pair() == target, 'normal selection disk commit changed')
        if owner.read_disk_receipt(original.operation_id) is None:
            owner.persist_disk_receipt_once(original.operation_id, receipt)
        guard.require(owner.read_disk_receipt(original.operation_id) == receipt,
                      'normal selection disk receipt differs')
        # Visible after rename is not necessarily durable after an interrupted
        # write. Sync the protected parent even when the receipt already exists.
        owner.sync_disk_receipt_parent(original.operation_id)
        guard.require(owner.read_disk_receipt(original.operation_id) == receipt,
                      'normal selection disk receipt changed during sync')
        # This port only observes the manager, while the existing writer
        # exclusion holds. Its sequential sample is not atomic or approval.
        manager = owner.observe_effective_normal(original.effective_original)
        verify_anchors()
        guard.require(pair() == target and owner.read_intent(original.operation_id) == intent
                      and owner.read_disk_receipt(original.operation_id) == receipt,
                      'normal selection changed during manager observation')
        return {'status': 'normal_pair_committed_on_disk',
                'receipt_sha256': guard.digest(receipt), 'manager': manager}
