from contextlib import ExitStack
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[2] / 'tools/delivery'))
import observe_guarded_normal_preimage as observe

g = observe.guard


class RetainedGuardedPreimage(unittest.TestCase):
    def test_expiry_and_exact_consumption_are_not_start_permission(self):
        intent = {'operation_id': 'a'*64, 'launch_before': 100}
        self.assertEqual(observe._retained_idle_status(intent, None, 99), 'potentially_actionable')
        self.assertEqual(observe._retained_idle_status(intent, None, 100), 'expired')
        exact = g.encode(g.start_consumption(intent, 42, 'b'*32))
        self.assertEqual(observe._retained_idle_status(intent, exact, 99), 'consumed')
        for raw in (b'{}', g.encode(g.start_consumption(intent, 42, '0'*32)),
                    g.encode(g.start_consumption({**intent, 'operation_id': 'c'*64}, 42, 'b'*32))):
            with self.assertRaises(g.Held):
                observe._retained_idle_status(intent, raw, 101)

    def test_expired_webhook_recognized_only_on_readback(self):
        plan = {key: 'a'*64 for key in g.WEBHOOK_PINS}
        approval = {**plan, 'schema': g.WEBHOOK_AUTH_SCHEMA, 'preflight_sha256': g.plan_digest(plan),
                    'job_id': '00000000-0000-0000-0000-000000000001', 'issued_at': 1, 'expires_at': 61}
        with patch.object(g.time, 'time', return_value=61), patch.object(g, 'read_record',
                                                                       return_value=g.encode(approval)):
            self.assertEqual(observe._retained_webhook_record(plan, approval), approval)
            with self.assertRaises(g.Held):
                g._verify_webhook_authorization(plan, approval)

    def test_fixed_typed_unit_and_retained_read_never_publish_or_start(self):
        pin = g.digest(g.selector_dropin())
        prior = observe.GuardedEffectivePrior(observe.effective.UNIT, pin, 'a'*64, 341, 2)
        condition = ('ConditionPathExists', False, False, str(g.STAGES / g.FIXED_STAGE / g.MARKER))
        exe = '/usr/bin/python3'
        argv = [exe, '-I', '-S', '-B', str(g.TOOL_PATH), '--profile', 'uif-idle',
                '--stage', g.FIXED_STAGE, '--action', 'serve']
        state = {'NeedDaemonReload': False, 'ActiveState': 'inactive', 'SubState': 'dead',
                 'MainPID': 0, 'ControlPID': 0, 'Job': [0, '/'], 'NRestarts': 2,
                 'ExecMainStartTimestampMonotonic': 341, 'ExecStart': [[exe, argv, False]],
                 'Conditions': [[*condition, 0]], 'Asserts': []}
        lock = object()
        with ExitStack() as stack:
            stack.enter_context(patch.object(g, 'bytes_pin', return_value=pin))
            manager = stack.enter_context(patch.object(observe.effective, '_state', return_value=state))
            read = stack.enter_context(patch.object(observe, 'observe_retained_idle_start', return_value='expired'))
            publish = stack.enter_context(patch.object(g, 'publish'))
            start = stack.enter_context(patch.object(g, 'serve_idle_stage'))
            def check():
                return observe.observe_guarded_preimage(lock, prior, conditions=(condition,), asserts=())
            self.assertTrue(check())
            read.assert_called_once_with(lock, 'a'*64)
            read.return_value = 'potentially_actionable'
            with self.assertRaisesRegex(g.Held, 'actionable'):
                check()
            read.return_value = 'consumed'
            self.assertTrue(check())
            failed = {**state, 'ActiveState': 'failed', 'SubState': 'failed'}
            manager.return_value = failed
            self.assertTrue(check())
            for delta in ({'SubState': 'dead'}, {'ActiveState': 'inactive'}, {'MainPID': 1},
                          {'ControlPID': 1}, {'Job': [1, '/test']}):
                manager.return_value = {**failed, **delta}
                with self.assertRaisesRegex(g.Held, 'manager preimage differs'):
                    check()
            manager.return_value = failed
            read.return_value = 'potentially_actionable'
            with self.assertRaisesRegex(g.Held, 'actionable'):
                check()
            read.return_value = 'consumed'
            manager.side_effect = [state, {**copy.deepcopy(state), 'Job': [1, '/test']}]
            with self.assertRaisesRegex(g.Held, 'bounded observation'):
                check()
            publish.assert_not_called()
            start.assert_not_called()
