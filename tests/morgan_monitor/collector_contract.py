"""Offline cross-language DTO contract validation. All data is synthetic."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

from tools.morgan_monitor.events import make_event, SOURCE_CAPABILITIES
from tools.morgan_monitor.sources import Observation

ROOT = Path(__file__).resolve().parents[2]
WORKER = ROOT / "services/morgan-monitor/worker.mjs"
NOW = 1794240000


@unittest.skipUnless(shutil.which("node") and WORKER.exists(), "Receiver source and Node required for offline contract check")
class ContractTests(unittest.TestCase):
    def test_all_collector_events_validate_against_receiver(self):
        cases = []
        for source, capability in SOURCE_CAPABILITIES.items():
            for health in ("ok", "unavailable", "unconfigured"):
                actual = "service_process" if capability == "service_process" else (capability if capability in {"none", "foo_allowance"} else "historical_registry")
                obs = Observation(source, NOW, health, actual, service_state="stopped" if actual == "service_process" and health == "ok" else None)
                bindings = []
                if health == "ok" and capability in {"registry", "turn_history"}:
                    obs.records = [{"key": "synthetic-private-identifier", "record_at": NOW - 700000, "state": "completed" if capability == "turn_history" else "recorded", "liveness": "unknown"}]
                    bindings = [{"sourceId": source, "recordKey": "synthetic-private-identifier", "alias": "1", "topicKeys": ["monitoring"], "blockerKeys": []}]
                cases.append({"event": make_event(obs, 1, bindings), "source": {"id": source, "domain": "cloud" if source == "cloud_feed" else "foo", "aliases": ["1"] if bindings else [], "capabilities": [] if capability == "none" else [capability]}})
        for state, can_run in (("valid", True), ("exhausted", False), ("unavailable", None)):
            obs = Observation("foo_allowance", NOW, "ok", "foo_allowance", allowance={"state": state, "canRunAgents": can_run, "observed_at": NOW})
            cases.append({"event": make_event(obs, 1, []), "source": {"id": "foo_allowance", "domain": "foo", "aliases": [], "capabilities": ["foo_allowance"]}})
        script = """
          import { readFileSync } from 'node:fs';
          import { validateEvent } from './services/morgan-monitor/worker.mjs';
          const cases = JSON.parse(readFileSync(0, 'utf8'));
          for (const {event, source} of cases) validateEvent(event, source, 1794240000 * 1000);
          process.stdout.write(JSON.stringify({validated: cases.length}));
        """
        result = subprocess.run(["node", "--input-type=module", "-e", script], input=json.dumps(cases), text=True, capture_output=True, cwd=ROOT, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"validated": 24})
