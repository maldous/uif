"""Verify the real Linux supervisor and original v1 receipt journal offline."""

from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import subprocess
import time
from unittest.mock import patch
from uuid import UUID

from test_publication import OPERATION, SNAPSHOT, git
from test_publication_rpc import RPCFixture
from uif.publication_rpc import GitHubPullRequests
from uif.native_publisher import (
    NativePublisher, PreparedCandidate, PreparedPublication, PreparedPullRequest, claim_binding, durable,
    process_start, read_optional, readiness_envelope, supervise,
)
from uif.publication import PublisherCommand, PublisherReceiptPaths, accepted_result, fingerprint, record_digest, wire
from uif.source import SourceHold, file_digest


class NativePublisherFixture(RPCFixture):
    stage_candidate = False

    def setUp(self):
        super().setUp()
        paths = PublisherReceiptPaths(self.root / "uif-accepted", self.root / "uif-claims")
        paths.accepted_root.mkdir(mode=0o700)
        (paths.accepted_root / "by-operation").mkdir(mode=0o700)
        paths.claim_root.mkdir(mode=0o700)
        (paths.claim_root / "repository-owner.lock").touch(mode=0o600)
        self.host = NativePublisher(paths, self.closure, self.pr.repository_id, os.getuid(), self.rpc, timeout=10,
                                    snapshot_path=SNAPSHOT if self.ledger_only else None)
        self.directory = paths.claim_root / ("transaction-" + self.request.publisher_operation_id)
        self.directory.mkdir(mode=0o700)
        source = self.candidate_bundle(self.directory / "candidate.bundle")
        self.host = replace(self.host, candidate_exclusions_sha256=source.exclusions_sha256)
        self.upload = PreparedCandidate(
            self.request.admission, self.request.issue, source, self.request.policy_sha256,
            self.request.validation_operation_id, self.request.validation_job_id,
            self.request.validation_receipt_sha256, self.request.publisher_operation_id, str(UUID(int=8)),
            task=self.request.task,
        )
        self.seal("candidate-binding.json", self.upload, {
            "schema": f"uif-prepared-candidate-publication/{self.request.version}", **self.upload.to_record(),
        })

    def seal(self, filename, request, grant, *, valid_until=None):
        path = self.directory / filename
        durable(path, grant, replace=path.exists())
        claim_path = self.host.paths.claim_root / "active-owner.json"
        claim = claim_binding(request, record_digest(grant),
                              self.valid_until if valid_until is None else valid_until)
        durable(claim_path, claim, replace=claim_path.exists())

    def setup_pr(self):
        uploaded = self.host.handle(wire(self.upload.envelope(observe=False)))["published_candidate"]
        self.prepare = PreparedPullRequest(
            self.request.admission, self.request.issue, self.candidate, self.request.policy_sha256,
            self.request.validation_operation_id, self.request.validation_job_id,
            self.request.validation_receipt_sha256, self.request.publisher_operation_id, str(UUID(int=9)),
            self.upload.invocation_id, uploaded["receipt_sha256"], task=self.request.task,
        )
        self.seal("pr-binding.json", self.prepare, {
            "schema": f"uif-prepared-pull-request/{self.request.version}", **self.prepare.to_record(),
        })

    def setup_readiness(self):
        self.setup_pr()
        result = self.host.handle(wire(self.prepare.envelope(observe=False)))
        prepared = result["prepared_pull_request"]
        readiness_grant = {
            "schema": f"uif-prepared-publisher-readiness/{self.request.version}", "request": self.request.to_record(),
            "pull_request": prepared["pull_request"], "test_merge_head": self.merge,
            "review_facts_sha256": self.review_sha, "require_atomic_base": False,
            "pr_preparation_invocation_id": self.prepare.invocation_id,
            "pr_preparation_receipt_sha256": prepared["receipt_sha256"],
        }
        readiness_grant["request"].pop("readiness_receipt_sha256")
        self.ready = PreparedPublication.parse(readiness_grant, readiness=True, snapshot_path=self.host.snapshot_path)
        self.seal("readiness-binding.json", self.request, readiness_grant)
        return readiness_grant

    def setup_final(self, *, atomic=False, mutate_grant=None):
        readiness_grant = self.setup_readiness()
        ready = self.host.handle(wire(readiness_envelope(self.ready, observe=False)))
        self.assertEqual(ready["response"]["status"], "ready")
        self.assertTrue(ready["receipt"]["body"]["terminal"]["waitpid_echild"])
        self.request = replace(self.request, readiness_receipt_sha256=ready["receipt"]["sha256"])
        grant = {**readiness_grant, "schema": f"uif-prepared-native-publication/{self.request.version}",
                 "request": self.request.to_record(), "require_atomic_base": atomic}
        if mutate_grant is not None:
            mutate_grant(grant)
        self.seal("binding.json", self.request, grant)
        return grant

    def invoke(self, *, observe=False):
        return self.host.handle(wire(self.request.envelope(observe=observe)))

class NativePublisherTests(NativePublisherFixture):
    def test_preparation_and_final_produce_original_readable_receipts(self):
        self.state.update(exists=False, create="lost")
        self.write_state()
        self.setup_final()
        result = self.invoke()
        accepted = accepted_result(result, self.request, self.request.envelope(observe=False), self.host)
        self.assertEqual(accepted.head, self.merge)
        self.assertTrue(result["receipt"]["body"]["terminal"]["waitpid_echild"])
        # Historical readback needs no current effect grant and starts no process.
        (self.host.paths.claim_root / "active-owner.json").unlink()
        self.assertEqual(self.invoke(observe=True), result)
        candidate = self.host.handle(wire(self.upload.envelope(observe=True)))
        self.assertEqual(candidate["published_candidate"]["source_head"], self.request.source_head)
        self.assertEqual(self.host.handle(wire(readiness_envelope(self.ready, observe=True)))["response"]["status"],
                         "ready")
        self.assertEqual(self.host.handle(wire(self.prepare.envelope(observe=True)))["prepared_pull_request"]["pull_request"],
                         asdict(self.pr))
        self.assertEqual([entry[0] for entry in self.writes()], ["POST", "PUT"])

    def test_repeated_invoke_and_observe_hold_original_uncertain_effect(self):
        self.state["merge"] = "refused"
        self.write_state()
        self.setup_final()
        original = self.invoke()
        self.assertEqual(original["response"]["status"], "unknown")
        self.assertNotIn("accepted_source", original)
        self.assertEqual(self.invoke(observe=True), original)
        self.assertEqual(self.invoke(), original)
        self.assertEqual([entry[0] for entry in self.writes()], ["PUT"])

    def test_missing_or_expired_active_claim_refuses_before_effect(self):
        self.setup_final()
        claim = self.host.paths.claim_root / "active-owner.json"
        payload = self.directory / "binding.json"
        self.seal(payload.name, self.request, json.loads(payload.read_bytes()), valid_until=1)
        self.assertTrue(claim.is_file())
        with self.assertRaisesRegex(SourceHold, "active_claim"):
            self.invoke()
        self.assertEqual(self.invoke(observe=True)["status"], "not-found")
        self.assertEqual(self.writes(), [])

    def test_atomic_expected_base_grant_is_explicitly_refused(self):
        self.setup_final(atomic=True)
        with self.assertRaisesRegex(SourceHold, "atomic_base_unsupported"):
            self.invoke()
        self.assertEqual(self.invoke(observe=True)["status"], "not-found")
        self.assertEqual(self.writes(), [])

    def test_final_grant_must_match_original_pr_validation_and_readiness(self):
        grant = self.setup_final(mutate_grant=lambda value: value.update(pr_preparation_receipt_sha256="1" * 64))
        with self.assertRaisesRegex(SourceHold, "original_pr_receipt"):
            self.invoke()
        pr_record = self.host.handle(wire(self.prepare.envelope(observe=True)))
        grant["pr_preparation_receipt_sha256"] = pr_record["receipt"]["sha256"]
        grant["request"]["validation_receipt_sha256"] = "2" * 64
        self.seal("binding.json", self.request, grant)
        with self.assertRaisesRegex(SourceHold, "original_pr_binding"):
            self.invoke()
        grant["request"]["validation_receipt_sha256"] = self.request.validation_receipt_sha256
        grant["request"]["readiness_receipt_sha256"] = "3" * 64
        self.seal("binding.json", self.request, grant)
        with self.assertRaisesRegex(SourceHold, "readiness_pin"):
            self.invoke()
        self.assertEqual(self.writes(), [])

    def test_original_uuid_cannot_rebind_after_intent(self):
        grant = self.setup_final()
        self.invoke()
        self.request = replace(self.request, invocation_id=str(UUID(int=12)))
        grant["request"] = asdict(self.request)
        self.seal("binding.json", self.request, grant)
        with self.assertRaisesRegex(SourceHold, "invocation_rebind"):
            self.invoke()
        self.assertEqual([entry[0] for entry in self.writes()], ["PUT"])

    def test_timed_out_supervisor_reaps_original_child_and_its_descendant(self):
        marker = self.root / "child-pid"

        def work():
            child = subprocess.Popen(["/bin/sleep", "20"])
            marker.write_text(str(child.pid))
            time.sleep(20)
            return {"status": "verified", "evidence": self.merge}

        terminal = supervise(work, self.request.envelope(observe=False)["request"], timeout=0.2)
        self.assertEqual(terminal["outcome"], "timed_out")
        self.assertTrue(terminal["child_tree_gone"])
        self.assertTrue(terminal["direct_child_reaped"])
        self.assertTrue(terminal["waitpid_echild"])
        self.assertIsNone(terminal["evidence"])
        self.assertFalse(Path("/proc", marker.read_text()).exists())

    def test_crash_between_durable_intent_and_receipt_stays_unknown_without_effect(self):
        grant = self.setup_final()
        inner = self.request.envelope(observe=False)["request"]
        original = {
            "version": 1, "kind": "native-publisher-invocation-record-v1", "terminal": None,
            "intent": {
                "operation_id": self.request.publisher_operation_id,
                "invocation_id": self.request.invocation_id, "request": inner,
                "request_sha256": fingerprint(inner), "phase": "integration",
                "closure_sha256": self.closure, "supervisor_pid": os.getpid(),
                "supervisor_starttime": process_start(os.getpid()),
            },
        }
        durable(self.directory / "native-final-intent.json",
                {"binding_sha256": record_digest(grant), "original": original})
        requests = self.config / "requests.jsonl"
        before = requests.read_bytes()
        observed = self.invoke(observe=True)
        self.assertEqual(observed["status"], "unknown")
        self.assertEqual(observed["receipt"]["body"], original)
        self.assertEqual(self.invoke(), observed)
        self.assertEqual(requests.read_bytes(), before)
        self.assertEqual(self.writes(), [])

    def test_repeated_candidate_invocation_never_republishes_a_missing_ref(self):
        first = self.host.handle(wire(self.upload.envelope(observe=False)))
        self.assertEqual(first["published_candidate"]["source_head"], self.request.source_head)
        git(self.bare, "update-ref", "-d", self.upload.candidate.ref)
        self.assertEqual(self.host.handle(wire(self.upload.envelope(observe=True))), first)
        self.assertEqual(self.host.handle(wire(self.upload.envelope(observe=False))), first)
        self.assertEqual(git(self.bare, "for-each-ref", "--format=%(refname)", self.upload.candidate.ref), "")

    def test_drifted_readiness_refuses_and_observation_does_not_launch_again(self):
        self.setup_readiness()
        self.state["comments"] = [[{"commit_id": self.request.source_head, "id": 1}]]
        self.write_state()
        first = self.host.handle(wire(readiness_envelope(self.ready, observe=False)))
        self.assertEqual(first["response"]["status"], "denied")
        self.assertTrue(first["receipt"]["body"]["terminal"]["waitpid_echild"])
        requests = self.config / "requests.jsonl"
        snapshot = requests.read_bytes()
        self.assertEqual(self.host.handle(wire(readiness_envelope(self.ready, observe=True))), first)
        self.assertEqual(self.host.handle(wire(readiness_envelope(self.ready, observe=False))), first)
        self.assertEqual(requests.read_bytes(), snapshot)
        self.assertEqual(self.writes(), [])

    def test_final_review_binding_cannot_change_after_qualification(self):
        self.setup_final(mutate_grant=lambda value: value.update(review_facts_sha256="3" * 64))
        with self.assertRaisesRegex(SourceHold, "original_readiness_binding"):
            self.invoke()
        self.assertEqual(self.writes(), [])

    def test_candidate_grant_cannot_omit_registered_exclusions(self):
        self.host = replace(self.host, candidate_exclusions_sha256="3" * 64)
        with self.assertRaisesRegex(SourceHold, "candidate_exclusion_policy"):
            self.host.handle(wire(self.upload.envelope(observe=False)))
        self.assertEqual(self.host.handle(wire(self.upload.envelope(observe=True)))["status"], "not-found")
        self.assertEqual(git(self.bare, "for-each-ref", "--format=%(refname)", self.upload.candidate.ref), "")

    def interrupt_facet(self, target):
        def interrupted(path, value, **kwargs):
            if path == target:
                raise OSError("interrupted facet")
            durable(path, value, **kwargs)
        return patch("uif.native_publisher.durable", side_effect=interrupted)

    def test_interrupted_pr_facet_recovers_original_bound_identity(self):
        self.state["exists"] = False
        self.write_state()
        self.setup_pr()
        path = self.directory / "native-pr-prepared.json"
        envelope = self.prepare.envelope(observe=False)
        with self.interrupt_facet(path), self.assertRaisesRegex(OSError, "interrupted facet"):
            self.host.handle(wire(envelope))
        self.assertFalse(path.exists())
        receipt = self.directory / f"native-invocation-{self.prepare.invocation_id}.json"
        original = read_optional(receipt, uid=self.host.uid)
        result = self.host.handle(wire(self.prepare.envelope(observe=True)))
        self.assertEqual(result["receipt"]["body"], original)
        self.assertEqual(original["terminal"]["evidence"], asdict(self.pr))
        self.assertEqual(result["prepared_pull_request"]["pull_request"], asdict(self.pr))
        self.assertEqual([entry[0] for entry in self.writes()], ["POST"])

    def test_interrupted_merge_facet_recovers_original_bound_head(self):
        self.setup_final()
        path = Path(self.host.paths.accepted(self.request))
        with self.interrupt_facet(path), self.assertRaisesRegex(OSError, "interrupted facet"):
            self.invoke()
        self.assertFalse(path.exists())
        original = read_optional(Path(self.host.paths.invocation(self.request)), uid=self.host.uid)
        observed = self.invoke(observe=True)
        self.assertEqual(observed["receipt"]["body"], original)
        self.assertEqual(original["terminal"]["evidence"], {"pull_request": asdict(self.pr), "merge_head": self.merge})
        self.assertEqual(accepted_result(observed, self.request, self.request.envelope(observe=True),
                                         self.host).head, self.merge)
        self.assertEqual([entry[0] for entry in self.writes()], ["PUT"])

    def test_durable_stop_can_reconcile_late_exact_merge_without_replaying(self):
        self.state["merge"] = "refused"
        self.write_state()
        self.setup_final()
        unknown = self.invoke()
        self.assertEqual(unknown["response"]["status"], "unknown")
        git(self.work, "push", "--quiet", str(self.bare), self.merge + ":refs/heads/develop")
        self.state["pr"].update(state="closed", merged=True, merged_at="2026-11-12T00:00:00Z",
                                merge_commit_sha=self.merge)
        self.write_state()
        observed = self.invoke(observe=True)
        self.assertEqual(observed["invocation_id"], unknown["invocation_id"])
        terminal = observed["receipt"]["body"]["terminal"]
        self.assertEqual(terminal["reconciliation"], {
            "original_terminal": unknown["receipt"]["body"]["terminal"],
            "original_receipt_sha256": unknown["receipt"]["sha256"],
        })
        self.assertEqual(accepted_result(observed, self.request, self.request.envelope(observe=True),
                                         self.host).head, self.merge)
        self.assertEqual(self.invoke(), observed)
        self.assertEqual([entry[0] for entry in self.writes()], ["PUT"])

    def test_substituted_pr_facet_is_refused_under_original_receipt(self):
        self.setup_pr()
        result = self.host.handle(wire(self.prepare.envelope(observe=False)))
        fact = result["prepared_pull_request"]
        fact["pull_request"]["number"] += 1
        fact["pull_request"]["pull_request_id"] += 1
        durable(self.directory / "native-pr-prepared.json", fact, replace=True)
        with self.assertRaisesRegex(SourceHold, "outcome_facet_drift"):
            self.host.handle(wire(self.prepare.envelope(observe=True)))

    def test_substituted_merge_facet_is_refused_under_original_receipt(self):
        self.setup_final()
        fact = self.invoke()["accepted_source"]["fact"]
        fact["head"] = "1" * 40
        durable(Path(self.host.paths.accepted(self.request)), fact, replace=True)
        with self.assertRaisesRegex(SourceHold, "outcome_facet_drift"):
            self.invoke(observe=True)

    def test_late_pr_readback_must_match_the_original_creation_reply(self):
        self.state["exists"] = False
        self.write_state()
        self.setup_pr()
        with patch("uif.publication_rpc.PublicationRPC.observe_prepared_pr", side_effect=SourceHold("unavailable")):
            original = self.host.handle(wire(self.prepare.envelope(observe=False)))
        self.assertEqual(original["response"]["status"], "unknown")
        self.assertEqual(original["receipt"]["body"]["terminal"]["evidence"], asdict(self.pr))
        self.state = json.loads(self.state_path.read_bytes())
        self.state["pr"]["id"] += 1
        self.write_state()
        observe = wire(self.prepare.envelope(observe=True))
        self.assertEqual(self.host.handle(observe), original)
        self.state["pr"]["id"] -= 1
        self.write_state()
        qualified = self.host.handle(observe)
        self.assertEqual(qualified["prepared_pull_request"]["pull_request"], asdict(self.pr))
        self.assertEqual([entry[0] for entry in self.writes()], ["POST"])


    def test_stopped_merge_timeout_reconciles_under_original_invocation(self):
        self.setup_final()

        class DelayedMerge(GitHubPullRequests):
            def merge(self, pin, expected_head):
                super().merge(pin, expected_head)
                time.sleep(15)
                raise SourceHold("lost reply")

        delayed = DelayedMerge(self.rpc.github.executable, self.rpc.github.config_dir)
        self.host = replace(self.host, rpc=replace(self.rpc, github=delayed), timeout=3)
        original = self.invoke()
        record = original["receipt"]["body"]
        terminal = record["terminal"]
        self.assertEqual(terminal["outcome"], "timed_out")
        self.assertTrue(terminal["child_tree_gone"])
        self.assertTrue(terminal["waitpid_echild"])
        self.assertIsNone(original["response"])
        self.assertEqual(git(self.bare, "rev-parse", "refs/heads/develop"), self.merge)
        log = self.config / "requests.jsonl"
        before = log.read_bytes()
        receipt = Path(self.host.paths.invocation(self.request))

        missing = {**record, "terminal": {**terminal, "child_tree_gone": False}}
        durable(receipt, missing, replace=True)
        self.assertEqual(self.invoke(observe=True)["status"], "unknown")
        self.assertEqual(log.read_bytes(), before)

        for change in ({"waitpid_echild": False},
                       {"child_pid": record["intent"]["supervisor_pid"]},
                       {"child_starttime": record["intent"]["supervisor_starttime"] - 1}):
            with self.subTest(change=change):
                durable(receipt, {**record, "terminal": {**terminal, **change}}, replace=True)
                with self.assertRaisesRegex(SourceHold, "stopped_child|stopped_timeout"):
                    self.invoke(observe=True)
                self.assertEqual(log.read_bytes(), before)

        durable(receipt, record, replace=True)
        observed = self.invoke(observe=True)
        self.assertGreater(len(log.read_bytes()), len(before))
        self.assertEqual(observed["invocation_id"], original["invocation_id"])
        proof = observed["receipt"]["body"]["terminal"]["reconciliation"]
        self.assertEqual(proof, {"original_terminal": terminal, "original_receipt_sha256": original["receipt"]["sha256"]})
        self.assertEqual(accepted_result(observed, self.request, self.request.envelope(observe=True),
                                         self.host).head, self.merge)
        self.assertEqual(self.invoke(), observed)
        self.assertEqual([entry[0] for entry in self.writes()], ["PUT"])


    def test_caller_deadline_preserves_original_supervisor_cleanup_and_receipt(self):
        self.setup_final()
        launcher = self.root / "delayed-publisher"
        attempts = self.root / "delayed-attempts"
        receipt_path = Path(self.host.paths.invocation(self.request))
        source = str(Path(__file__).resolve().parents[2] / "src")
        launcher.write_text(
            "#!/usr/bin/python3 -B\n"
            "import json, os, subprocess, sys, time\n"
            "from pathlib import Path\n"
            f"sys.path.insert(0, {source!r})\n"
            "from uif.native_publisher import NativePublisher\n"
            "from uif.publication import PublisherReceiptPaths, wire\n"
            "from uif.source import SourceHold\n"
            "class DelayedRPC:\n"
            "    def publish(self, *args, **kwargs):\n"
            "        child = subprocess.Popen(['/bin/sleep', '4'])\n"
            f"        with open({str(attempts)!r}, 'a') as log:\n"
            "            log.write(json.dumps([os.getpid(), child.pid]) + '\\n')\n"
            "        time.sleep(4)\n"
            "        raise AssertionError('disposable worker should time out')\n"
            "    def observe_merge(self, *args, **kwargs):\n"
            "        raise SourceHold('fixture has no merge')\n"
            f"paths = PublisherReceiptPaths(Path({str(self.host.paths.accepted_root)!r}), "
            f"Path({str(self.host.paths.claim_root)!r}))\n"
            f"host = NativePublisher(paths, {self.closure!r}, {self.pr.repository_id}, os.getuid(), "
            "DelayedRPC(), timeout=2)\n"
            "sys.stdout.buffer.write(wire(host.handle(sys.stdin.buffer.read())))\n"
        )
        launcher.chmod(0o700)
        command = PublisherCommand(launcher, file_digest(launcher), self.closure, self.host.paths, timeout=1)
        with self.assertRaisesRegex(SourceHold, "uif_publisher_outcome_unknown"):
            command.call(self.request.envelope(observe=False))

        deadline = time.monotonic() + 6
        record = None
        while time.monotonic() < deadline:
            record = read_optional(receipt_path, uid=os.getuid())
            if (record is not None and record["terminal"] is not None
                    and not Path("/proc", str(record["intent"]["supervisor_pid"])).exists()):
                break
            time.sleep(0.02)
        self.assertIsNotNone(record)
        self.assertIsNotNone(record["terminal"], "the original supervisor did not persist its terminal receipt")
        terminal = record["terminal"]
        self.assertEqual(terminal["outcome"], "timed_out")
        self.assertTrue(terminal["direct_child_reaped"])
        self.assertTrue(terminal["waitpid_echild"])
        self.assertTrue(terminal["child_tree_gone"])
        child_pids, = attempts.read_text().splitlines()
        for pid in json.loads(child_pids):
            self.assertFalse(Path("/proc", str(pid)).exists())

        observed = replace(command, timeout=5).call(self.request.envelope(observe=True))
        self.assertEqual(observed["invocation_id"], self.request.invocation_id)
        self.assertEqual(observed["receipt"], {"body": record, "sha256": record_digest(record)})
        self.assertEqual(attempts.read_text().splitlines(), [child_pids])
        self.assertEqual(self.writes(), [])


class LedgerNativePublisherTests(NativePublisherFixture):
    ledger_only = True

    def test_issue_free_candidate_pr_readiness_and_merge_with_original_recovery(self):
        self.state.update(exists=False, merge="refused")
        self.write_state()
        self.setup_final()
        original = self.invoke()
        self.assertEqual(original["response"]["status"], "unknown")
        git(self.work, "push", "--quiet", str(self.bare), self.merge + ":refs/heads/develop")
        self.state = json.loads(self.state_path.read_bytes())
        self.state["pr"].update(state="closed", merged=True, merged_at="2026-11-12T00:00:00Z",
                                merge_commit_sha=self.merge)
        self.write_state()
        observed = self.invoke(observe=True)
        task = self.request.task.to_record()
        self.assertEqual(task["original_operation_id"], OPERATION)
        self.assertEqual(observed["invocation_id"], original["invocation_id"])
        self.assertEqual(observed["version"], 2)
        self.assertNotIn("issue_id", observed["response"])
        self.assertEqual(observed["response"]["task"], task)
        self.assertEqual(observed["accepted_source"]["fact"]["task"], task)
        self.assertEqual(observed["accepted_source"]["fact"]["workspace_key"], self.request.task.key.workspace_key)
        accepted = accepted_result(observed, self.request, self.request.envelope(observe=True), self.host)
        self.publisher.remote.verify(accepted)
        self.assertEqual(accepted.head, self.merge)
        self.assertEqual([entry[0] for entry in self.writes()], ["POST", "PUT"])

    def test_candidate_grant_needs_registered_snapshot_and_rejects_mixed_wire(self):
        envelope = self.upload.envelope(observe=False)
        with self.assertRaisesRegex(SourceHold, "source_grant_schema"):
            replace(self.host, snapshot_path=None).handle(wire(envelope))
        envelope["request"]["issue_id"] = str(UUID(int=5))
        with self.assertRaisesRegex(SourceHold, "grant_drift"):
            self.host.handle(wire(envelope))
        self.assertEqual(self.writes(), [])
