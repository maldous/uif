"""Synthetic only. No production roster, credentials, network, or live output."""
import asyncio
import copy
import json
from pathlib import Path
import shutil
import subprocess
import time
import unittest

from tools.morgan_monitor.durable_capture import capture, CaptureError

NOW = 1794225600
PRIVATE = "DO_NOT_PUBLISH_PRIVATE_CONTENT"


def binding(alias="A", thread="synthetic-thread-a"):
    return {"threadId": thread, "alias": alias, "topicKeys": ["monitoring"]}


def response(thread="synthetic-thread-a", status="inProgress"):
    return {"isError": False, "structuredContent": {
        "threadId": thread, "parentThreadId": PRIVATE,
        "latestTurn": {"id": "private-turn-id", "status": status, "error": PRIVATE},
        "items": [{"text": PRIVATE, "title": PRIVATE, "url": PRIVATE}], "nextCursor": PRIVATE},
        "content": [{"type": "text", "text": PRIVATE}]}


class CaptureTests(unittest.IsolatedAsyncioTestCase):
    async def event(self, value=None, bindings=None, **kwargs):
        async def reader(thread):
            return copy.deepcopy(response(thread) if value is None else value)
        return await capture(reader, [binding()] if bindings is None else bindings,
                             roster_generation=3, sequence=17, clock=lambda: NOW, **kwargs)

    async def test_current_recorded_turn_mapping_and_private_field_exclusion(self):
        for status, state in (("inProgress", "unknown"), ("completed", "completed"),
                              ("failed", "unknown"), ("interrupted", "unknown")):
            event = await self.event(response(status=status))
            row = event["sessions"][0]
            self.assertEqual(state, row["state"])
            self.assertEqual(["execution_failed"] if status == "failed" else [], row["blockerKeys"])
            self.assertEqual("coordinator_lifecycle", row["proof"])
            serialized = json.dumps(event)
            for private in (PRIVATE, "synthetic-thread-a", "private-turn-id", "error", "items", "environmentKey"):
                self.assertNotIn(private, serialized)
            self.assertFalse(event["coverage"]["complete"])
            self.assertEqual(3, event["rosterGeneration"])

    async def test_failed_quota_message_cannot_become_allowance_or_capacity(self):
        value = response(status="failed")
        value["structuredContent"]["latestTurn"]["error"] = "usage limit reached; try again tomorrow"
        event = await self.event(value)
        self.assertEqual(["execution_failed"], event["sessions"][0]["blockerKeys"])
        self.assertNotIn("quota", json.dumps(event))
        self.assertNotIn("fooAllowance", event)

    async def test_missing_turn_unknown_is_not_unassigned(self):
        value = response()
        value["structuredContent"]["latestTurn"] = None
        row = (await self.event(value))["sessions"][0]
        self.assertEqual("unknown", row["state"])
        self.assertEqual("assigned", row["assignment"])

    async def test_malformed_unknown_and_identity_mismatch_fail_closed(self):
        cases = [{}, {"content": [{"type": "text", "text": json.dumps(response())}]},
                 {**response(), "isError": True}, response(thread="wrong-thread"),
                 response(status="new-status"), response(status=["inProgress"])]
        missing = response()
        del missing["structuredContent"]["latestTurn"]
        cases.append(missing)
        for case in cases:
            event = await self.event(case)
            self.assertEqual("error", event["health"])
            self.assertEqual([], event["sessions"])
            self.assertEqual({"complete": False, "observed": 0, "total": None}, event["coverage"])

    async def test_partial_read_omits_only_unavailable_slot(self):
        calls = []
        async def reader(thread):
            calls.append(thread)
            if thread == "thread-b":
                raise RuntimeError(PRIVATE)
            return response(thread)
        event = await capture(reader, [binding("F", "thread-f"), binding("B", "thread-b")],
                              roster_generation=3, sequence=1, clock=lambda: NOW)
        self.assertCountEqual(["thread-f", "thread-b"], calls)
        self.assertEqual(["F"], [row["alias"] for row in event["sessions"]])
        self.assertEqual("ok", event["health"])
        self.assertFalse(event["coverage"]["complete"])
        self.assertNotIn(PRIVATE, json.dumps(event))

    async def test_empty_bindings_make_no_read(self):
        async def reader(thread):
            self.fail("No reader call permitted with an unconfigured roster")
        event = await capture(reader, [], roster_generation=1, sequence=1, clock=lambda: NOW)
        self.assertEqual("unconfigured", event["health"])
        self.assertEqual([], event["sessions"])

    async def test_timeout_is_unavailable_not_inactive(self):
        async def reader(thread):
            await asyncio.sleep(0.2)
            return response(thread)
        event = await capture(reader, [binding()], roster_generation=1, sequence=1,
                              clock=lambda: NOW, timeout_seconds=0.01)
        self.assertEqual("error", event["health"])
        self.assertEqual([], event["sessions"])

    async def test_cancellation_suppressing_reader_cannot_delay_or_publish_late(self):
        release, finished = asyncio.Event(), asyncio.Event()
        async def reader(thread):
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                await release.wait()
            finally:
                finished.set()
            return response(thread, "completed")
        try:
            event = await asyncio.wait_for(capture(reader, [binding()],
                roster_generation=1, sequence=1, clock=lambda: NOW,
                timeout_seconds=0.01), timeout=0.5)
            self.assertEqual("error", event["health"])
            self.assertEqual([], event["sessions"])
            self.assertFalse(finished.is_set())
        finally:
            release.set()
            await asyncio.wait_for(finished.wait(), timeout=0.5)
        self.assertEqual([], event["sessions"])

    async def test_late_blocking_callback_result_is_not_accepted(self):
        async def reader(thread):
            time.sleep(0.02)
            return response(thread, "completed")
        event = await capture(reader, [binding()], roster_generation=1, sequence=1,
                              clock=lambda: NOW, timeout_seconds=0.005)
        self.assertEqual("error", event["health"])
        self.assertEqual([], event["sessions"])

    async def test_cancel_propagates(self):
        async def reader(thread):
            raise asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await capture(reader, [binding()], roster_generation=1, sequence=1, clock=lambda: NOW)

    async def test_each_new_capture_requires_new_read(self):
        count = 0
        async def reader(thread):
            nonlocal count
            count += 1
            return response(thread)
        for instant in (NOW, NOW + 10):
            event = await capture(reader, [binding()], roster_generation=1, sequence=count + 1,
                                  clock=lambda: instant)
            self.assertEqual(event["observedAt"], event["sessions"][0]["observedAt"])
        self.assertEqual(2, count)

    async def test_capture_timestamps_are_read_completion_not_old_response_dates(self):
        times = iter((NOW, NOW + 1, NOW + 2))
        async def reader(thread):
            value = response(thread)
            value["structuredContent"]["observedAt"] = "2100-01-01T00:00:00Z"
            return value
        event = await capture(reader, [binding()], roster_generation=1, sequence=1, clock=lambda: next(times))
        self.assertLess(event["sessions"][0]["observedAt"], event["observedAt"])
        self.assertNotIn("2100", json.dumps(event))

    async def test_clock_rollback_and_out_of_bounds_fail_entire_capture(self):
        for values in ((NOW, NOW - 1), (NOW, NOW + 8), (NOW, NOW + 1, NOW)):
            times = iter(values)
            async def reader(thread):
                return response(thread)
            with self.assertRaises(CaptureError):
                await capture(reader, [binding()], roster_generation=1, sequence=1, clock=lambda: next(times))
        for bad in (True, float("nan"), float("inf"), 0):
            with self.assertRaises(CaptureError):
                await capture(reader, [binding()], roster_generation=1, sequence=1, clock=lambda: bad)

    async def test_invalid_roster_fails_before_any_read(self):
        cases = [[binding(), binding()], [binding(), binding("B")], [binding("G")],
                 [{**binding(), "environmentKey": "foo_uif"}],
                 [{**binding(), "topicKeys": [PRIVATE]}],
                 [{**binding(), "topicKeys": ["monitoring", "monitoring"]}],
                 [{**binding(), "alias": ""}], [{**binding(), "threadId": "https://private"}]]
        async def reader(thread):
            self.fail("Invalid configuration must not reach source")
        for value in cases:
            with self.assertRaises(CaptureError):
                await capture(reader, value, roster_generation=1, sequence=1, clock=lambda: NOW)

    async def test_invalid_generation_sequence_and_timeout_fail_before_read(self):
        async def reader(thread):
            self.fail("Invalid configuration must not reach source")
        for key, value in (("roster_generation", 0), ("roster_generation", True),
                           ("sequence", 0), ("sequence", 2**53), ("sequence", False),
                           ("timeout_seconds", 11), ("timeout_seconds", float("nan"))):
            config = {"roster_generation": 1, "sequence": 1, key: value}
            with self.assertRaises(CaptureError):
                await capture(reader, [binding()], clock=lambda: NOW, **config)

    async def test_binding_mutation_during_read_does_not_change_projection(self):
        roster = [binding()]
        async def reader(thread):
            roster[0]["alias"] = "B"
            roster[0]["topicKeys"][0] = PRIVATE
            return response(thread)
        event = await capture(reader, roster, roster_generation=1, sequence=1, clock=lambda: NOW)
        self.assertEqual("A", event["sessions"][0]["alias"])
        self.assertEqual(["monitoring"], event["sessions"][0]["topicKeys"])

    @unittest.skipUnless(shutil.which("node"), "Node unavailable for receiver cross-contract")
    async def test_all_shapes_pass_pinned_receiver_validator(self):
        events = [await self.event(response(status=status)) for status in
                  ("inProgress", "completed", "failed", "interrupted", "unrecognized")]
        events.append(await self.event(bindings=[]))
        root = Path(__file__).resolve().parents[2]
        worker = root / "services/morgan-monitor/worker.mjs"
        if not worker.is_file():
            self.skipTest("Pinned receiver source unavailable")
        script = """
          import {validateEvent} from './services/morgan-monitor/worker.mjs';
          let input=''; for await (const part of process.stdin) input+=part;
          const source={id:'coordinator_observer', domain:'cloud', aliases:[...'ABCDEF'],
            capabilities:['coordinator_lifecycle'], rosterGeneration:3};
          for(const event of JSON.parse(input)) validateEvent(event, source, Date.parse(event.observedAt));
          console.log('6 projected event shapes accepted');
        """
        result = subprocess.run(["node", "--input-type=module", "-e", script], cwd=root,
                                input=json.dumps(events), text=True, capture_output=True, timeout=10)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("6 projected event shapes accepted", result.stdout)


if __name__ == "__main__":
    unittest.main()
