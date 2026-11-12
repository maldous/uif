import asyncio
import hashlib
import json
from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from uif.role import verify_control_role
from uif.source import SourceHold, file_digest, git, verify_checkout
from uif.stokowski import (
    UIF, PolledIssue, Profile, QualifiedContext, SelectedSource, SourceBoundary, Workspace,
    acquire_uif_task_source, bind_workspace_receiver,
)


ROOT = Path(__file__).resolve().parents[2]


class SourceControlTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.seed = self.root / "seed"
        self.seed.mkdir(mode=0o700)
        git(self.seed, "init", "--quiet", "--initial-branch=develop", "--template=")
        git(self.seed, "remote", "add", "origin", UIF.origin)
        (self.seed / "control.py").write_text("value = 'UIF fixture'\n")
        (self.seed / "control-link").symlink_to("control.py")
        git(self.seed, "add", "control.py", "control-link")
        git(self.seed, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "commit", "-qm", "UIF source fixture")
        self.head = git(self.seed, "rev-parse", "HEAD")
        self.tree = git(self.seed, "rev-parse", "HEAD^{tree}")
        self.evidence = self.root / "accepted.json"
        self.evidence.write_text(json.dumps({"fixture": True, "head": self.head, "tree": self.tree}))
        self.evidence.chmod(0o600)
        self.coordinator = self.root / "coordinator"
        self.coordinator.mkdir(mode=0o700)
        self.runtime = self.root / "task"
        self.runtime.mkdir(mode=0o700)
        (self.runtime / "workspaces").mkdir(mode=0o700)
        self.checkout = self.root / "selected"
        self.branch = "bugfix/owner-policy-uif-900001"
        self.issue = PolledIssue("UIF-900001", "1765ee92-55d6-4012-a889-95e65555711d",
                                 UIF.project_id, UIF.project_slug)
        self.active = True
        self.calls = []

    def guard(self):
        if not self.active:
            raise SourceHold("fixture_original_operation_requires_reconciliation")

    def acquire(self, *, tree=None):
        return acquire_uif_task_source(self.seed, self.checkout, head=self.head, tree=tree or self.tree,
                                       branch=self.branch, evidence=self.evidence,
                                       sha256=file_digest(self.evidence), owner_guard=self.guard)

    def boundary(self, *, optional=(), profile=UIF, stage=False, drift=False):
        receipt = self.root / "acquisition.json"
        receipt.write_text(json.dumps({"fixture": True, "checkout": str(self.checkout)}))
        receipt.chmod(0o600)
        selected = SelectedSource(profile.repository, self.head, self.tree, self.branch, self.checkout,
                                  profile.accepted_branch, self.evidence, file_digest(self.evidence),
                                  receipt, file_digest(receipt))
        task = {"issue_id": self.issue.issue_id}
        if stage:
            task["stage"] = "review"
        manifest = {"project_id": profile.project_id, "tasks": {self.issue.identifier: task},
                    "base_revision": self.head, "branch": self.branch}

        def context(issue):
            self.guard()
            self.calls.append(("qualified", issue.project_id))
            return QualifiedContext(self.runtime, manifest, self.checkout)

        def source(route, issue):
            self.calls.append(("selected", route.repository))
            return replace(selected, tree="0" * 40) if drift and len(self.calls) > 2 else selected

        return SourceBoundary(runtime_root=self.root, coordinator_runtime=self.coordinator,
                              qualify_context=context, selected_source=source, optional_profiles=optional)

    def workspace(self, boundary, issue=None):
        return boundary.ensure_workspace(issue or self.issue, self.coordinator / "workspaces")

    def test_normal_uif_path_requires_no_product_checkout(self):
        seed_before = git(self.seed, "show-ref")
        self.assertTrue(self.acquire())
        self.assertFalse(self.acquire())
        result = self.workspace(self.boundary())
        self.assertTrue(result.created)
        verify_checkout(result.path, self.head, self.tree, self.branch)
        self.assertEqual((result.path / "control-link").readlink(), Path("control.py"))
        self.assertFalse(self.workspace(self.boundary()).created)
        self.assertEqual(git(self.seed, "show-ref"), seed_before)
        self.assertEqual(git(self.seed, "branch", "--show-current"), "develop")

    def test_existing_stokowski_hooks_receive_qualified_uif_workspace(self):
        self.acquire()
        orchestrator = SimpleNamespace()
        workspace_module = SimpleNamespace(WorkspaceResult=Workspace)
        boundary = self.boundary()
        bind_workspace_receiver(orchestrator, workspace_module, boundary=boundary,
                                polled_issue=lambda identifier: self.issue)
        root = self.coordinator / "workspaces"
        result = asyncio.run(orchestrator.ensure_workspace(root, self.issue.identifier, {}))
        self.assertTrue(result.created)
        self.assertEqual(workspace_module.ensure_workspace, orchestrator.ensure_workspace)
        self.assertEqual(result.path, self.runtime / "workspaces" / self.issue.identifier)
        with self.assertRaisesRegex(SourceHold, "workspace_polled_issue_binding"):
            asyncio.run(orchestrator.ensure_workspace(root, "UIF-900002", {}))

    def test_optional_project_route_is_selected_explicitly(self):
        profile = Profile(**json.loads((ROOT / "tests/delivery/fixtures/optional-project-profile.json").read_bytes()))
        issue = replace(self.issue, project_id=profile.project_id, project_slug=profile.project_slug)
        with self.assertRaisesRegex(SourceHold, "unconfigured_project_route"):
            self.workspace(self.boundary(), issue)
        self.assertEqual(self.calls, [])
        # This uses a synthetic sealed checkout. The product's existing source
        # accessor remains responsible for product Git provenance.
        self.acquire()
        self.assertTrue(self.workspace(self.boundary(optional=(profile,), profile=profile), issue).created)
        self.assertIn(("selected", profile.repository), self.calls)

    def test_wrong_tree_or_origin_prevents_acquisition(self):
        with self.assertRaisesRegex(SourceHold, "source_ref_head_tree"):
            self.acquire(tree="0" * 40)
        git(self.seed, "remote", "set-url", "origin", "https://example.invalid/another/project")
        with self.assertRaisesRegex(SourceHold, "accepted_uif_source_drift"):
            self.acquire()
        self.assertFalse(self.checkout.exists())

    def test_input_drift_blocks_workspace_and_preserves_retained_work(self):
        self.acquire()
        boundary = self.boundary()
        self.evidence.write_text("changed accepted facts")
        with self.assertRaisesRegex(SourceHold, "sealed_task_source_evidence"):
            self.workspace(boundary)
        result = self.workspace(self.boundary())
        (result.path / "unpublished").write_text("retain me")
        with self.assertRaisesRegex(SourceHold, "workspace_requires_reconciliation"):
            self.workspace(self.boundary())
        self.assertEqual((result.path / "unpublished").read_text(), "retain me")

    def test_selected_source_requires_local_git_objects(self):
        self.acquire()
        boundary = self.boundary()
        objects = self.checkout / ".git/objects"
        retained = objects.rename(self.root / "retained-selected-objects")
        objects.symlink_to(retained, target_is_directory=True)

        with self.assertRaisesRegex(SourceHold, "source_shared_git_storage"):
            self.workspace(boundary)
        self.assertFalse((self.runtime / "workspaces" / self.issue.identifier).exists())
        self.assertEqual(objects.readlink(), retained)

    def test_acquisition_requires_local_git_metadata(self):
        for relative in ("refs", "objects/info"):
            with self.subTest(relative=relative):
                entry = self.seed / ".git" / relative
                retained = entry.rename(self.root / ("retained-" + entry.name))
                entry.symlink_to(retained, target_is_directory=True)
                try:
                    with self.assertRaisesRegex(SourceHold, "source_shared_git_storage"):
                        self.acquire()
                    self.assertFalse(self.checkout.exists())
                    self.assertEqual(entry.readlink(), retained)
                finally:
                    entry.unlink()
                    retained.rename(entry)

    def test_git_storage_indirection_files_remain_refused(self):
        for relative in ("commondir", "objects/info/alternates"):
            with self.subTest(relative=relative):
                path = self.seed / ".git" / relative
                path.write_text("unselected-storage\n")
                try:
                    with self.assertRaisesRegex(SourceHold, "source_shared_git_storage"):
                        self.acquire()
                    self.assertFalse(self.checkout.exists())
                finally:
                    path.unlink()

    def test_reused_workspace_requires_local_git_objects(self):
        self.acquire()
        boundary = self.boundary()
        result = self.workspace(boundary)
        objects = result.path / ".git/objects"
        retained = objects.rename(self.root / "retained-workspace-objects")
        objects.symlink_to(retained, target_is_directory=True)

        with self.assertRaisesRegex(SourceHold, "source_shared_git_storage"):
            self.workspace(boundary)
        self.assertEqual(objects.readlink(), retained)
        self.assertTrue(retained.is_dir())

    def test_changed_binding_and_stage_refuse_workspace(self):
        self.acquire()
        with self.assertRaisesRegex(SourceHold, "workspace_selection_changed"):
            self.workspace(self.boundary(drift=True))
        with self.assertRaisesRegex(SourceHold, "nonimplementation_workspace_refused"):
            self.workspace(self.boundary(stage=True))
        self.active = False
        with self.assertRaisesRegex(SourceHold, "requires_reconciliation"):
            self.workspace(self.boundary())
        self.assertFalse((self.runtime / "workspaces" / self.issue.identifier).exists())

    def test_role_manifest_does_not_need_optional_profile(self):
        path = ROOT / "config/delivery/stokowski-control.json"
        files = verify_control_role(ROOT, path, file_digest(path))
        self.assertIn("src/uif/stokowski.py", files)
        self.assertNotIn("tests/delivery/fixtures/optional-project-profile.json", files)
        with self.assertRaisesRegex(SourceHold, "control_role_manifest_pin"):
            verify_control_role(ROOT, path, hashlib.sha256(b"wrong").hexdigest())
