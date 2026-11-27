from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import Mock

from test_native_remote_authority import FENCE, GRANT, ISSUE, RECORD, make
from uif.handoff_store import Hold
from uif.stokowski_cloudflare.native_composition import NativeComposition, _COMMENT_SLOTS, install_sinks
from uif.stokowski_cloudflare.remote_authority import AuthorityUnavailable


class NativeGateReceiverTest(IsolatedAsyncioTestCase):
    async def test_gate_post_checks_current_fence_after_await_and_recovers_same_original(self):
        wire, remote = make()
        wire.claim = RECORD
        composition = NativeComposition(remote, '/unread-fixture')
        composition.assignment = lambda *a: GRANT
        composition.retained_linear = Mock()  # Disk custody covered in existing tests.
        transport = wire.request
        calls = []
        lost_once = True

        def request(path, *args, **kwargs):
            nonlocal lost_once
            result = transport(path, *args, **kwargs)
            if path == '/v1/linear/comment':
                calls.append(args[0]['operation'])
                if lost_once:
                    lost_once = False
                    raise AuthorityUnavailable('fixture-lost-ack')
            return result

        wire.request = request
        gate = SimpleNamespace(after_wait=lambda: None)

        async def enter(owner):
            await gate.after_wait()
            return await orchestration.LinearClient().post_comment(ISSUE, 'same gate note')

        async def unused(*a, **kw):
            raise AssertionError('unexpected scheduler or publication hook')

        native = type('Native', (), {**dict.fromkeys(_COMMENT_SLOTS, unused),
            '_enter_gate': enter, '_reconcile': unused, '_resume_settlement': unused})
        orchestration = SimpleNamespace(Orchestrator=native, LinearClient=lambda: None)
        install_sinks(orchestration, composition)

        async def revoke():
            wire.claim = replace(RECORD, lease=replace(RECORD.lease,
                fence=replace(FENCE, generation=FENCE.generation + 1)))
        gate.after_wait = revoke
        with self.assertRaisesRegex(Hold, 'authority_original_lease_changed'):
            with _binding(composition):
                await native()._enter_gate()
        self.assertEqual(calls, [])
        composition.retained_linear.assert_not_called()

        async def unchanged():
            return None
        gate.after_wait = unchanged
        wire.claim = RECORD
        with _binding(composition):
            with self.assertRaises(AuthorityUnavailable):
                await native()._enter_gate()
            self.assertTrue(await native()._enter_gate())
        self.assertEqual(len(calls), 1)
        one, two = [arg.args[0] for arg in composition.retained_linear.call_args_list]
        self.assertEqual(one, two)
        self.assertIsNone(composition._slot.get())


@contextmanager
def _binding(composition):
    token = composition._running.set((GRANT, RECORD))
    try:
        yield
    finally:
        composition._running.reset(token)
