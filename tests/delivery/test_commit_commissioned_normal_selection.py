from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[2] / 'tools/delivery'))
import commit_commissioned_normal_selection as commit
from install_guarded_idle_bootstrap import _publish_once, _sync_parent
from read_normal_effective_state import NormalEffectiveOriginal
from stage_commissioned_normal_runtime import NormalRuntimeStageApproval

g = commit.guard


class PrivateOwnerFixture:
    """Real ordinary-user filesystem; no host or real manager."""
    def __init__(self, temp):
        self.temp = temp
        self.legacy = temp/'legacy'
        self.selector = temp/'selector-dir'/'selector'
        self.selector.parent.mkdir()
        self.guarded_selector = temp/'guarded-selector'
        self.actionable_start = False
        self.receipt_syncs = []
        self.selector_syncs = 0
        self.guarded_selector.write_bytes(b'old guarded selector')
        self.guard_observations = []
        self.intent_syncs = []
        self.dropin = temp/'dropin-dir'/'dropin'
        self.dropin.parent.mkdir()
        self.stage = temp/'staged'
        self.lock = temp/'lock'
        self.legacy.write_bytes(g.selector_dropin())
        self.lock.touch()
        self.effects = []
        self.observations = []

    @contextmanager
    def selector_lock(self):
        with self.lock.open('rb') as fd:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield

    @staticmethod
    def read(path):
        return path.read_bytes() if path.exists() else None

    def guarded_dropin(self):
        return self.legacy.read_bytes()

    def verify_staged(self, receipt, selection):
        return self.stage.read_bytes() == receipt

    def read_guarded_selector(self):
        return self.read(self.guarded_selector)

    def verify_guarded_effective(self, prior):
        # Test-local evidence. Production port must attest the actual manager's
        # effective command and quiescence/history and absence of actionable start.
        self.guard_observations.append(prior)
        return (prior.unit == commit.normal.UNIT and prior.dropin_sha256 == g.digest(g.selector_dropin())
                and prior.selector_sha256 == g.digest(b'old guarded selector') and not self.actionable_start
                and prior.before_start_monotonic == prior.before_restarts == 0)

    def read_selector(self):
        return self.read(self.selector)

    def read_normal_dropin(self):
        return self.read(self.dropin)

    def persist_once(self, path, raw):
        if not path.exists():
            _publish_once(path, 0o400, lambda out:out.write(raw))
        assert self.read(path) == raw

    def read_intent(self, op):
        return self.read(self.temp/(op+'.intent'))

    def persist_intent_once(self, op, raw):
        self.persist_once(self.temp/(op+'.intent'), raw)
        self.effects.append('intent')

    def sync_intent_parent(self, op):
        _sync_parent(self.temp/(op+'.intent'))
        self.intent_syncs.append(op)

    def publish_selector(self, prior, raw):
        assert commit._pin(self.read_selector()) == prior
        self.persist_once(self.selector, raw)
        self.effects.append('selector')

    def publish_normal_dropin(self, prior, raw):
        assert commit._pin(self.read_normal_dropin()) == prior
        self.persist_once(self.dropin, raw)
        self.effects.append('dropin')

    def sync_selector_parent(self):
        _sync_parent(self.selector)
        self.selector_syncs += 1

    def sync_target_parents(self):
        _sync_parent(self.selector)
        _sync_parent(self.dropin)

    def persist_disk_receipt_once(self, op, raw):
        self.persist_once(self.temp/(op+'.committed'), raw)

    def sync_disk_receipt_parent(self, op):
        _sync_parent(self.temp/(op+'.committed'))
        self.receipt_syncs.append(op)

    def read_disk_receipt(self, op):
        return self.read(self.temp/(op+'.committed'))

    def observe_effective_normal(self, original):
        self.observations.append((self.read_selector(), self.read_normal_dropin()))
        return {'status': 'disk_matches_manager_reload_needed', 'installation_accepted': False}


class NormalSelectionCommit(unittest.TestCase):
    def setUp(self):
        tmp = Path(self.enterContext(TemporaryDirectory()))
        self.enterContext(patch.object(g, 'protected_parents', return_value=None))
        self.owner = PrivateOwnerFixture(tmp)
        stage_leaf = 'uif1313-normal-fixture'
        stage_path = str(g.STAGES/stage_leaf)
        prior = g.digest(g.selector_dropin())
        staged = NormalRuntimeStageApproval('c'*64, stage_leaf, 'd'*64, 'e'*64, prior, g.digest(b'old guarded selector'))
        self.owner.stage.write_bytes(g.encode({'schema': 'uif.foo-normal-runtime-staged/1', **vars(staged)}))
        self.selector = commit.normal.render_normal_selection(stage_path, source_head='1'*40,
            source_tree='2'*40, release_sha256=staged.members_sha256, launcher_sha256='3'*64,
            interpreter_sha256='4'*64, authority_config_sha256='5'*64)
        self.dropin = commit.normal.render_normal_dropin(stage_path)
        pin = g.digest(self.dropin)
        manager = NormalEffectiveOriginal(stage_path, pin, pin, 0, 0, (), ())
        self.original = commit.SelectionOriginal('a'*64, staged, None, None, prior,
                                                  g.digest(self.selector), pin, manager)

    def apply(self):
        return commit.commit_normal_selection(self.original, self.selector, self.dropin, self.owner)

    def test_publish_order_and_same_original_no_repeat_manager_mutation(self):
        self.assertNotIn(commit.normal.SELECTION.encode(), g.selector_dropin())
        result = self.apply()
        self.assertEqual(self.owner.effects, ['intent','selector','dropin'])
        self.assertEqual(self.owner.observations, [(self.selector, self.dropin)])
        self.assertEqual(result['manager']['status'], 'disk_matches_manager_reload_needed')
        self.assertEqual(self.apply(), result)
        self.assertEqual(self.owner.effects, ['intent','selector','dropin'])

    def test_lost_ack_after_selector_reconciles_same_original_before_dropin(self):
        real_publish = self.owner.publish_selector
        def lost(*args):
            real_publish(*args)
            raise OSError('lost acknowledgement')
        with patch.object(self.owner, 'publish_selector', side_effect=lost):
            with self.assertRaisesRegex(OSError, 'lost acknowledgement'):
                self.apply()
        self.assertEqual(self.owner.read_selector(), self.selector)
        self.assertIsNone(self.owner.read_normal_dropin())
        self.assertFalse(self.owner.observations)
        self.assertEqual(self.owner.guarded_dropin(), g.selector_dropin())
        self.apply()
        self.assertEqual(self.owner.effects, ['intent','selector','dropin'])

    def test_visible_unsynced_selector_orders_parent_sync_before_dropin(self):
        def interrupted(prior, raw):
            self.assertIsNone(prior)
            temporary = self.owner.selector.with_name('selector-temporary')
            with temporary.open('xb') as output:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
            temporary.rename(self.owner.selector)
            raise OSError('selector parent not synced')
        with patch.object(self.owner, 'publish_selector', side_effect=interrupted):
            with self.assertRaisesRegex(OSError, 'selector parent not synced'):
                self.apply()
        before = self.owner.selector.stat().st_ino
        self.assertEqual(self.owner.read_selector(), self.selector)
        self.assertIsNone(self.owner.read_normal_dropin())
        self.assertEqual(self.owner.selector_syncs, 0)

        with patch.object(self.owner, 'sync_selector_parent', side_effect=OSError('sync failed')):
            with self.assertRaisesRegex(OSError, 'sync failed'):
                self.apply()
        self.assertIsNone(self.owner.read_normal_dropin())

        real = self.owner.publish_normal_dropin
        def check_sync(*args):
            self.assertEqual(self.owner.selector_syncs, 1)
            self.assertEqual(self.owner.selector.stat().st_ino, before)
            return real(*args)
        with (patch.object(self.owner, 'publish_selector') as selector_publish,
              patch.object(self.owner, 'publish_normal_dropin', side_effect=check_sync)):
            self.apply()
        selector_publish.assert_not_called()
        self.assertEqual(self.owner.read_normal_dropin(), self.dropin)

    def test_reversed_or_foreign_change_before_original_never_repaired(self):
        self.owner.dropin.write_bytes(self.dropin)
        for blob in (None, b'foreign'):
            if blob is not None:
                self.owner.selector.write_bytes(blob)
            with self.assertRaisesRegex(g.Held, 'no original'):
                self.apply()
        self.assertEqual(self.owner.read_selector(), b'foreign')
        self.assertEqual(self.owner.read_normal_dropin(), self.dropin)
        self.assertFalse(self.owner.effects)
        self.assertFalse(self.owner.observations)

    def test_lost_ack_after_dropin_reobserves_pair_without_republishing(self):
        real = self.owner.publish_normal_dropin
        def lost(*args):
            real(*args)
            raise OSError('disk effect acknowledged late')
        with patch.object(self.owner, 'publish_normal_dropin', side_effect=lost):
            with self.assertRaises(OSError):
                self.apply()
        self.assertEqual((self.owner.read_selector(), self.owner.read_normal_dropin()),
                         (self.selector,self.dropin))
        self.assertFalse(self.owner.observations)
        self.apply()
        self.assertEqual(self.owner.effects, ['intent','selector','dropin'])


    def test_visible_unsynced_intent_requires_parent_sync_before_effect(self):
        op = self.original.operation_id
        def interrupted(operation, raw):
            # Simulate visible filename after rename but before syncing parent.
            temporary = self.owner.temp/'temporary'
            temporary.write_bytes(raw)
            temporary.rename(self.owner.temp/(operation+'.intent'))
            raise OSError('intent parent not synced')
        with patch.object(self.owner, 'persist_intent_once', side_effect=interrupted):
            with self.assertRaisesRegex(OSError, 'intent parent not synced'):
                self.apply()
        self.assertIsNotNone(self.owner.read_intent(op))
        self.assertFalse(self.owner.effects)
        real = self.owner.publish_selector
        def check_sync(*args):
            self.assertEqual(self.owner.intent_syncs, [op])
            return real(*args)
        with patch.object(self.owner, 'publish_selector', side_effect=check_sync):
            self.apply()
        self.assertEqual(self.owner.effects, ['selector','dropin'])

    def test_old_guarded_selector_missing_or_changed_holds_before_effect(self):
        self.owner.guarded_selector.unlink()
        with self.assertRaisesRegex(g.Held, 'guarded profile'):
            self.apply()
        self.owner.guarded_selector.write_bytes(b'other incompatible selector')
        with self.assertRaisesRegex(g.Held, 'guarded profile'):
            self.apply()
        self.assertFalse(self.owner.effects)

    def test_old_manager_and_actionable_marker_refusal_are_not_old_pointer_equivalence(self):
        with patch.object(self.owner,'verify_guarded_effective', return_value=False) as attest:
            with self.assertRaisesRegex(g.Held, 'effective preimage'):
                self.apply()
        prior = attest.call_args.args[0]
        self.assertEqual(prior.selector_sha256, self.original.staged.prior_selection_sha256)
        self.owner.actionable_start = True
        with self.assertRaisesRegex(g.Held, 'effective preimage'):
            self.apply()
        self.assertFalse(self.owner.effects)

    def test_visible_unsynced_receipt_reconciled_without_republishing(self):
        op = self.original.operation_id
        def interrupted(operation, raw):
            temporary = self.owner.temp/'receipt-temporary'
            with temporary.open('xb') as output:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
            temporary.rename(self.owner.temp/(operation+'.committed'))
            raise OSError('receipt parent not synced')
        with patch.object(self.owner, 'persist_disk_receipt_once', side_effect=interrupted):
            with self.assertRaisesRegex(OSError, 'receipt parent not synced'):
                self.apply()
        receipt_path = self.owner.temp/(op+'.committed')
        before = receipt_path.stat().st_ino
        self.assertFalse(self.owner.observations)
        self.assertFalse(self.owner.receipt_syncs)
        # The old write-edge proof is not a new post-commit gate.
        with (patch.object(self.owner, 'persist_disk_receipt_once') as publish,
              patch.object(self.owner, 'verify_guarded_effective') as guarded):
            result = self.apply()
        publish.assert_not_called()
        guarded.assert_not_called()
        self.assertEqual(receipt_path.stat().st_ino, before)
        self.assertEqual(self.owner.receipt_syncs, [op])
        self.assertEqual(result['receipt_sha256'], g.digest(receipt_path.read_bytes()))

    def test_foreign_disk_drift_during_manager_observation_never_reports_success(self):
        def drift(_):
            replacement = self.owner.temp/'foreign-selector'
            replacement.write_bytes(b'foreign after manager observes')
            replacement.replace(self.owner.selector)
            return {'status': 'synthetic success'}
        with patch.object(self.owner,'observe_effective_normal', side_effect=drift):
            with self.assertRaisesRegex(g.Held, 'changed during manager'):
                self.apply()
        with self.assertRaisesRegex(g.Held, 'mixed or foreign'):
            self.apply()
        self.assertEqual(self.owner.read_selector(), b'foreign after manager observes')
