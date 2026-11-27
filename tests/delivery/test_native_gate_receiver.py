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


class NativeUpstreamGatePipelineTest(IsolatedAsyncioTestCase):
    async def test_actual_upstream_gate_keeps_first_clock_and_current_scope(self):
        import ast
        from datetime import datetime, timezone
        from hashlib import sha256
        import json
        import os
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from typing import Any

        root = os.environ.get('UIF_PINNED_STOKOWSKI_SOURCE')
        if root is None:
            self.skipTest('set UIF_PINNED_STOKOWSKI_SOURCE to verified 339cbf4 source')

        def program(name, pin, fn, cls=None):
            raw = (Path(root) / name).read_bytes()
            self.assertEqual(sha256(raw).hexdigest(), pin)
            node = ast.parse(raw)
            nodes = node.body if cls is None else next(c.body for c in node.body
                if isinstance(c, ast.ClassDef) and c.name == cls)
            function = next(x for x in nodes if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef))
                            and x.name == fn)
            return compile(ast.Module(body=[function], type_ignores=[]), name, 'exec')

        class Clock:
            next = 0

            @classmethod
            def now(cls, tz):
                cls.next += 1
                return datetime(2030, 1, 1, 10, 0, cls.next, tzinfo=tz)

        namespace = dict(Any=Any, datetime=Clock, timezone=timezone, json=json,
                         Issue=object, logger=Mock(),
                         _resolve_linear_state_name=lambda key, _: key)
        exec(program('tracking.py', '4d166a6b2fbcdddd99a15f048bce01c43d69c55b5e87e152709570e6c590a14d',
                     'make_gate_comment'), namespace)
        exec(program('orchestrator.py', 'ed7493b1aac58711c6edbdb1ed4b9260f00bfe831331409a7b85796b4fee7408',
                     '_enter_gate', 'Orchestrator'), namespace)

        with TemporaryDirectory() as tmp:
            (Path(tmp) / '.git').mkdir()
            wire, remote = make()
            wire.claim = RECORD
            composition = NativeComposition(remote, '/unread-fixture')
            composition.assignment = lambda *a: GRANT
            composition.installation = lambda: {'assignments': {ISSUE: {
                'workspace_binding': {'path': tmp}}}}
            original = wire.request
            sends = []

            def request(path, *args, **kw):
                result = original(path, *args, **kw)
                if path == '/v1/linear/comment':
                    sends.append(args[0])
                    raise AuthorityUnavailable('fixture-lost-ack')
                return result
            wire.request = request

            async def unused(*args):
                raise AssertionError('unexpected hook')
            native = type('Native', (), {**dict.fromkeys(_COMMENT_SLOTS, unused),
                '_enter_gate': namespace['_enter_gate'], '_reconcile': unused, '_resume_settlement': unused})
            orchestration = SimpleNamespace(Orchestrator=native, LinearClient=lambda: None)
            install_sinks(orchestration, composition)

            gate = SimpleNamespace(prompt='Original assistant gate prompt', linear_state='review')
            issue = SimpleNamespace(id=ISSUE, identifier='OFF-1')
            instance = native()
            instance.cfg = SimpleNamespace(linear_states={})
            instance._states_for = lambda _: {'review':gate}
            instance._issue_state_runs = {}
            instance._ensure_linear_client = orchestration.LinearClient
            instance._pending_gates, instance._issue_current_state = {}, {}
            instance.running, instance._tasks, instance.claimed = {}, {}, {ISSUE}
            instance._release_slot, instance._schedule_retry = Mock(), Mock()

            with _binding(composition):
                with self.assertRaises(AuthorityUnavailable):
                    await instance._enter_gate(issue, 'review')
                self.assertEqual(instance._pending_gates, {})
                await instance._enter_gate(issue, 'review')
                self.assertEqual(instance._pending_gates, {ISSUE: 'review'})
                self.assertIn(ISSUE, instance.claimed)  # Unsupported state mutation stays refused.
                self.assertEqual(len(sends), 1)
                self.assertIn('2030-01-01T10:00:01+00:00', sends[0]['body'])
                gate.prompt = 'Different prompt'
                with self.assertRaisesRegex(Hold, 'gate_semantics_changed'):
                    await instance._enter_gate(issue, 'review')
                gate.prompt = 'Original assistant gate prompt'
                instance._issue_state_runs[ISSUE] = 2
                with self.assertRaisesRegex(Hold, 'gate_semantics_changed'):
                    await instance._enter_gate(issue, 'review')
                instance._issue_state_runs[ISSUE] = 1
                journal = Path(tmp)/'.git/uif-original-effects'
                sidecar = next(journal.glob('*.gate-clock'))
                assert sidecar.stat().st_size < 8192
                sidecar.unlink()  # simulate an original predating clock retention
                with self.assertRaisesRegex(Hold, 'verified_different_original_payload'):
                    await instance._enter_gate(issue, 'review')
                self.assertFalse(sidecar.exists())
                wire.claim = replace(RECORD, lease=replace(RECORD.lease,
                    fence=replace(FENCE, generation=FENCE.generation+1)))
                with self.assertRaisesRegex(Hold, 'original_lease_changed'):
                    await instance._enter_gate(issue, 'review')
                self.assertEqual(len(sends), 1)
                self.assertEqual(len(list((Path(tmp)/'.git/uif-original-effects').glob('*.linear'))), 1)


class NativeGateClockCustodyTest(IsolatedAsyncioTestCase):
    async def test_atomic_clock_winner_custody_and_stale_after_write(self):
        import json
        import os
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        from uif.handoff_store import decode, wire
        from uif.stokowski_cloudflare import native_composition as adapter

        stamp = '2030-01-01T10:00:02+00:00'
        first = '2030-01-01T10:00:01+00:00'
        body = ('<!-- stokowski:gate ' + json.dumps(
            dict(state='review', status='waiting', run=1, timestamp=stamp)) + ' -->\n\nSame text')

        for case in ('winner', 'corrupt', 'oversize', 'symlink', 'stale'):
            with self.subTest(case=case), TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root/'.git').mkdir()
                rpc, remote = make()
                rpc.claim = RECORD
                composition = NativeComposition(remote, '/unread-fixture')
                composition.assignment = lambda *a: GRANT
                composition.installation = lambda: {'assignments': {ISSUE: {
                    'workspace_binding': {'path': tmp}}}}
                publish = adapter._publish_gate_clock
                sends = []
                transport = rpc.request

                def observed(path, *args, **kw):
                    if path == '/v1/linear/comment':
                        sends.append(args[0])
                    return transport(path, *args, **kw)
                rpc.request = observed

                def competing(fd, name, raw):
                    if case == 'stale':
                        publish(fd, name, raw)
                        rpc.claim = replace(RECORD, lease=replace(RECORD.lease,
                            fence=replace(FENCE, generation=FENCE.generation + 1)))
                        return
                    if case == 'symlink':
                        os.symlink('/not-the-record', name, dir_fd=fd)
                    elif case == 'oversize':
                        h = os.open(name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600, dir_fd=fd)
                        with os.fdopen(h, 'wb') as f:
                            f.write(b'x' * 8193)
                    else:
                        record = decode(raw, limit=8192)
                        record['timestamp'] = first
                        if case == 'corrupt':
                            record['semantic_sha256'] = '0'*64
                        publish(fd, name, wire(record))
                    raise FileExistsError()

                slot = composition._slot.set('_enter_gate')
                try:
                    with patch.object(adapter, '_publish_gate_clock', side_effect=competing), _binding(composition):
                        if case == 'winner':
                            self.assertTrue(composition.linear('comment', ISSUE, {'body':body}))
                            self.assertEqual(len(sends), 1)
                            self.assertIn(first, sends[0]['body'])
                        else:
                            with self.assertRaises((Hold, OSError)):
                                composition.linear('comment', ISSUE, {'body':body})
                            self.assertFalse(sends)
                finally:
                    composition._slot.reset(slot)
