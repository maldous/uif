"""Run the UIF publisher command and readback against disposable Git state."""

from contextlib import contextmanager
from dataclasses import replace
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from unittest import TestCase
from uuid import UUID

from uif.ledger import SNAPSHOT_SHA256, read_snapshot
from uif.publication import (
    GitReadback, PublicationRequest, Publisher, PublisherCommand, PublisherReceiptPaths, accepted_result,
    fingerprint, record_digest,
)
from uif.source import SourceHold, file_digest
from uif.stokowski import LedgerAdmission, PolledIssue, UIF

SNAPSHOT = Path(__file__).resolve().parents[2] / "README.md"
STORE = "fixture-original-uif-store"
PREDECESSOR = "src-060-placement-decision"
OPERATION = "original-admission-operation"


def git(repo, *args):
    return subprocess.check_output(
        ["/usr/bin/git", "-C", str(repo), *args], text=True, stderr=subprocess.PIPE,
    ).strip()


class ExistingOwner:
    def __init__(self, request):
        self.current = request
        self.intent = None
        self.mode = None

    @contextmanager
    def fence(self, request, mode):
        assert self.mode is None
        self.mode = mode
        try:
            self.recheck(request)
            yield self
        finally:
            self.mode = None

    def recheck(self, request):
        if self.mode is None or (self.mode in ("active", "recovery") and request != self.current):
            raise SourceHold("owner_epoch_changed")

    def read_intent(self, key, operation):
        if self.intent and self.intent.key == key and self.intent.admission.original_operation_id == operation:
            return self.intent
        return None

    def create_intent(self, request):
        self.recheck(request)
        assert self.mode == "active" and self.intent is None
        self.intent = request


class PublicationFixture(TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.work = self.root / "work"
        self.work.mkdir()
        self.bare = self.root / "canonical.git"
        git(self.root, "init", "--quiet", "--bare", str(self.bare))
        git(self.work, "init", "--quiet", "--initial-branch=develop")
        git(self.work, "config", "user.name", "UIF fixture")
        git(self.work, "config", "user.email", "fixture@example.invalid")
        git(self.work, "commit", "--quiet", "--allow-empty", "-m", "accepted base")
        base = git(self.work, "rev-parse", "HEAD")
        git(self.work, "switch", "--quiet", "-c", "feature/test")
        (self.work / "fixture").write_text("approved output\n")
        git(self.work, "add", "fixture")
        git(self.work, "commit", "--quiet", "-m", "reviewed head")
        head = git(self.work, "rev-parse", "HEAD")
        tree = git(self.work, "rev-parse", "HEAD^{tree}")
        git(self.work, "switch", "--quiet", "develop")
        git(self.work, "merge", "--quiet", "--no-ff", "-m", "accepted integration", head)
        self.merge = git(self.work, "rev-parse", "HEAD")
        git(self.work, "push", "--quiet", str(self.bare), f"{base}:refs/heads/develop")

        row = read_snapshot(SNAPSHOT)[PREDECESSOR]
        admission = LedgerAdmission(
            STORE, SNAPSHOT_SHA256, PREDECESSOR, row.source_sha256, "implementation", "owner",
            "epoch-3", OPERATION, "ready", "confirmed-absent", False,
        )
        issue = PolledIssue("UIF-123", str(UUID(int=1)), UIF.project_id, UIF.project_slug)
        source = {
            "repository": UIF.repository, "branch": "feature/test", "base": base,
            "head": head, "tree": tree, "target": UIF.accepted_branch, "policy_sha256": "a" * 64,
        }
        self.request = PublicationRequest(
            admission=admission, issue=issue, source_base=base, source_head=head, source_tree=tree,
            source_branch=source["branch"], source_sha256=fingerprint(source),
            policy_sha256=source["policy_sha256"], unit_sha256="b" * 64, packet_sha256="c" * 64,
            capacity_lease_id="coding-lease", validation_operation_id="d" * 64,
            validation_job_id=str(UUID(int=2)), validation_receipt_sha256="e" * 64,
            review_operation_id="review-operation", reviewer="independent-reviewer",
            review_receipt_sha256="f" * 64, readiness_invocation_id=str(UUID(int=3)),
            readiness_receipt_sha256="1" * 64, publisher_operation_id="2" * 64,
            invocation_id=str(UUID(int=4)),
        )
        self.owner = ExistingOwner(self.request)
        self.closure = "3" * 64
        self.paths = PublisherReceiptPaths(self.root / "accepted", self.root / "claim")
        self.log = self.root / "calls"
        self.reply = self.root / "reply.json"
        self.lose_reply = self.root / "lose-reply"
        executable = self.root / "publisher"
        executable.write_text(
            "#!/usr/bin/python3\n"
            "import json, subprocess, sys\n"
            "from pathlib import Path\n"
            "envelope = json.load(sys.stdin)\n"
            f"with open({str(self.log)!r}, 'a') as f:\n"
            "    f.write(json.dumps(envelope) + '\\n')\n"
            "if envelope['mode'] == 'invoke':\n"
            f"    subprocess.run(['/usr/bin/git', '-C', {str(self.work)!r}, 'push', '--quiet', "
            f"{str(self.bare)!r}, {self.merge + ':refs/heads/develop'!r}], check=True)\n"
            f"    if Path({str(self.lose_reply)!r}).exists():\n"
            "        sys.exit(5)\n"
            f"print(Path({str(self.reply)!r}).read_text())\n",
        )
        executable.chmod(0o700)
        self.publisher = Publisher(self.owner.fence, PublisherCommand(
            executable, file_digest(executable), self.closure, self.paths), GitReadback(self.bare))
        self.write_reply(self.result())

    def result(self, *, stopped=True):
        inner = self.request.envelope(observe=False)["request"]
        request_sha = fingerprint(inner)
        response = {
            **{key: val for key, val in inner.items() if key != "mode"},
            "kind": "native-publisher-response-v1", "phase": "integration",
            "request_sha256": request_sha, "status": "verified",
        }
        record = {
            "version": 1, "kind": "native-publisher-invocation-record-v1",
            "intent": {
                "request": inner, "request_sha256": request_sha, "closure_sha256": self.closure,
                "invocation_id": self.request.invocation_id, "operation_id": self.request.publisher_operation_id,
                "phase": "integration", "supervisor_pid": 14, "supervisor_starttime": 17,
            },
            "terminal": {
                "outcome": "succeeded", "child_started": True, "child_pid": 15, "child_starttime": 18,
                "subreaper_verified": True, "direct_child_reaped": True, "waitpid_echild": stopped,
                "child_tree_gone": stopped, "response": response, "response_sha256": fingerprint(response),
                "evidence": {"pull_request": {"repository_id": 321, "number": 123, "pull_request_id": 789},
                             "merge_head": self.merge},
            },
        }
        fact = {
            "schema": "uif-accepted-project-source/1", "repository": UIF.repository,
            "branch": UIF.accepted_branch, "head": self.merge, "tree": self.request.source_tree,
            "parents": [self.request.source_base, self.request.source_head],
            "base": self.request.source_base, "candidate_head": self.request.source_head,
            "policy_sha256": self.request.policy_sha256,
            "pr_url": f"https://github.com/{UIF.repository}/pull/123",
            "helper_receipt": {"path": self.paths.invocation(self.request), "sha256": record_digest(record)},
            **{key: inner[key] for key in ("operation_id", "unit_sha256", "packet_sha256")},
        }
        return {
            "version": 1, "kind": "native-publisher-invocation-v1",
            "request_sha256": request_sha, "invocation_id": self.request.invocation_id,
            "operation_id": self.request.publisher_operation_id, "status": "complete",
            "response": response, "receipt": {"body": record, "sha256": record_digest(record)},
            "accepted_source": {"fact": fact, "reference": {
                "path": self.paths.accepted(self.request), "sha256": record_digest(fact),
            }},
        }

    def write_reply(self, value):
        self.reply.write_text(json.dumps(value))

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []


class PublicationTests(PublicationFixture):
    def test_publisher_observes_existing_intent_with_original_inner_request(self):
        accepted = self.publisher.consume_boundary(self.request)
        self.assertEqual(accepted.head, self.merge)
        self.assertEqual(self.owner.intent, self.request)
        self.assertEqual(self.publisher.consume_boundary(self.request), accepted)
        self.assertEqual(self.calls(), [self.request.envelope(observe=False), self.request.envelope(observe=True)])

    def test_rejects_a_fact_without_actual_remote_readback(self):
        self.owner.intent = self.request
        with self.assertRaisesRegex(SourceHold, "remote_readback"):
            self.publisher.consume_boundary(self.request)
        self.assertEqual(self.calls(), [self.request.envelope(observe=True)])

    def test_requires_stopped_child_and_preserves_the_original_intent(self):
        self.write_reply(self.result(stopped=False))
        with self.assertRaisesRegex(SourceHold, "terminal_proof"):
            self.publisher.consume_boundary(self.request)
        self.assertEqual(self.owner.intent, self.request)
        self.assertEqual(self.calls(), [self.request.envelope(observe=False)])

    def test_rejects_rebinding_invocation_or_superseded_epoch(self):
        self.owner.intent = self.request
        other = replace(self.request, invocation_id=str(UUID(int=5)))
        self.owner.current = other
        with self.assertRaisesRegex(SourceHold, "intent_drift"):
            self.publisher.consume_boundary(other)
        another_publisher = replace(other, publisher_operation_id="9" * 64)
        self.owner.current = another_publisher
        with self.assertRaisesRegex(SourceHold, "intent_drift"):
            self.publisher.consume_boundary(another_publisher)
        with self.assertRaisesRegex(SourceHold, "owner_epoch_changed"):
            self.publisher.consume_boundary(self.request)
        self.assertEqual(self.calls(), [])

    def test_publisher_pin_mismatch_holds_before_effect(self):
        self.publisher.command.executable.write_text("#!/usr/bin/python3\n")
        with self.assertRaisesRegex(SourceHold, "command_pin"):
            self.publisher.consume_boundary(self.request)
        self.assertEqual(self.calls(), [])

    def test_retains_known_publication_after_the_branch_advances(self):
        accepted = self.publisher.consume_boundary(self.request)
        git(self.work, "commit", "--quiet", "--allow-empty", "-m", "subsequent accepted source")
        git(self.work, "push", "--quiet", str(self.bare), "develop")
        self.assertEqual(self.publisher.consume_boundary(self.request), accepted)

    def test_facet_cannot_substitute_pr_or_reachable_merge_under_original_receipt(self):
        accepted = self.publisher.consume_boundary(self.request)
        alternate = git(self.work, "commit-tree", self.request.source_tree,
                        "-p", self.request.source_base, "-p", self.request.source_head,
                        "-m", "different merge of the same source")
        later = git(self.work, "commit-tree", self.request.source_tree, "-p", self.merge,
                    "-p", alternate, "-m", "canonical continuation")
        git(self.work, "push", "--quiet", str(self.bare), later + ":refs/heads/develop")
        self.publisher.remote.verify(replace(accepted, head=alternate))
        for name, substitute in (("pr_url", accepted.pr_url.removesuffix("/123") + "/124"), ("head", alternate)):
            with self.subTest(field=name):
                reply = self.result()
                original_digest = reply["receipt"]["sha256"]
                facet = reply["accepted_source"]
                facet["fact"][name] = substitute
                facet["reference"]["sha256"] = record_digest(facet["fact"])
                self.assertEqual(reply["receipt"]["sha256"], original_digest)
                with self.assertRaisesRegex(SourceHold, "terminal_evidence"):
                    accepted_result(reply, self.request, self.request.envelope(observe=True), self.publisher.command)
        self.assertEqual(self.publisher.consume_boundary(self.request), accepted)

    def test_holds_legacy_and_malformed_bound_evidence(self):
        for evidence in (None, {"merge_head": self.merge, "pull_request": {
                "repository_id": True, "number": 123, "pull_request_id": 789}}):
            with self.subTest(evidence=evidence):
                reply = self.result()
                record = reply["receipt"]["body"]
                if evidence is None:
                    record["terminal"].pop("evidence")
                else:
                    record["terminal"]["evidence"] = evidence
                pin = record_digest(record)
                reply["receipt"]["sha256"] = pin
                facet = reply["accepted_source"]
                facet["fact"]["helper_receipt"]["sha256"] = pin
                facet["reference"]["sha256"] = record_digest(facet["fact"])
                with self.assertRaisesRegex(SourceHold, "terminal_evidence"):
                    accepted_result(reply, self.request, self.request.envelope(observe=True), self.publisher.command)

    def test_reconciled_timeout_requires_original_stop_witness(self):
        for damage in (None, "waitpid_echild", "child_pid", "digest", "missing_original"):
            with self.subTest(damage=damage):
                reply = self.result()
                record = reply["receipt"]["body"]
                terminal = record["terminal"]
                original = dict(terminal, outcome="timed_out", response=None, response_sha256=None, evidence=None)
                if damage == "waitpid_echild":
                    original["waitpid_echild"] = False
                elif damage == "child_pid":
                    original["child_pid"] += 1
                original_pin = record_digest({**record, "terminal": original})
                terminal["reconciliation"] = {
                    "original_terminal": original,
                    "original_receipt_sha256": original_pin if damage != "digest" else "0" * 64,
                }
                if damage == "missing_original":
                    terminal["reconciliation"].pop("original_terminal")
                pin = record_digest(record)
                reply["receipt"]["sha256"] = pin
                facet = reply["accepted_source"]
                facet["fact"]["helper_receipt"]["sha256"] = pin
                facet["reference"]["sha256"] = record_digest(facet["fact"])
                envelope = self.request.envelope(observe=True)
                if damage is None:
                    accepted = accepted_result(reply, self.request, envelope, self.publisher.command)
                    self.assertEqual((accepted.head, accepted.invocation_receipt_sha256), (self.merge, pin))
                else:
                    with self.assertRaisesRegex(SourceHold, "reconciliation_proof"):
                        accepted_result(reply, self.request, envelope, self.publisher.command)

    def test_rejects_a_valid_hash_at_the_wrong_accepted_receipt_path(self):
        reply = self.result()
        reply["accepted_source"]["reference"]["path"] += ".copied"
        self.write_reply(reply)
        with self.assertRaisesRegex(SourceHold, "accepted_source"):
            self.publisher.consume_boundary(self.request)

    def test_rejects_available_commit_with_wrong_merge_identity(self):
        reply = self.result()
        accepted = reply["accepted_source"]
        accepted["fact"]["head"] = self.request.source_base
        receipt = reply["receipt"]
        receipt["body"]["terminal"]["evidence"]["merge_head"] = self.request.source_base
        receipt["sha256"] = record_digest(receipt["body"])
        accepted["fact"]["helper_receipt"]["sha256"] = receipt["sha256"]
        accepted["reference"]["sha256"] = record_digest(accepted["fact"])
        self.write_reply(reply)
        with self.assertRaisesRegex(SourceHold, "readback_source"):
            self.publisher.consume_boundary(self.request)
