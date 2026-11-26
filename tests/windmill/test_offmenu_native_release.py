import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[2] / "config/windmill/source"
with patch.object(sys, "path", [str(SOURCE), *sys.path]):
    import offmenu_native_release as native
    import offmenu_release_journal as journal

ONE = "12345678-1234-1234-1234-123456789abc"
TWO = "12345678-1234-1234-1234-123456789def"


class NativeReleaseTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixed = native.ProtectedOffMenuApproval("operator", *("a"*64, "b"*64,
            "c"*64, "d"*64), native.OFFMENU_SOURCE, native.OFFMENU_TREE)
        self.request = {key: getattr(self.fixed, key) for key in native.FIELDS}

    def bind(self, canonical, approval=None):
        def bind_private(approved, _package, _upgrade):
            self.assertEqual(tuple(self.fixed), approved)
            return canonical
        ingress = SimpleNamespace(
            REPOSITORY=native.REPOSITORY, SERVICE="offmenu-stokowski-current.service",
            TARGET="/opt/offmenu-publisher", WindmillReleaseApproval=lambda *args: args,
            bind_private_windmill_release=bind_private,
        )
        return native.bind_fixed_native_handler(approval or self.fixed, ingress, object(), object())

    def invoke(self, handler, job):
        with patch.object(journal, "_open_protected",
                          lambda _: os.open(self.temp.name, os.O_RDONLY | os.O_DIRECTORY)):
            return handler(native.PROFILE, job, self.request)

    def test_actual_composition_validates_native_receipt_across_instances(self):
        calls = []
        def canonical(action, operation, job):
            calls.append((action, job))
            already = any(name == "replace" for name, _ in calls)
            return dict(state="installed" if already else "not_started",
                        operation_id=operation, confirmation_sha256="d"*64)
        self.assertEqual("installed", self.invoke(self.bind(canonical), ONE))
        self.assertEqual("installed", self.invoke(self.bind(canonical), TWO))
        self.assertEqual([("status", ONE), ("replace", ONE), ("status", ONE), ("status", TWO)], calls)

    def test_wrong_receipt_and_wrong_source_do_not_dispatch(self):
        def canonical(action, operation, job):
            self.assertEqual("status", action)
            return dict(state="not_started", operation_id=operation, confirmation_sha256="e"*64)
        with self.assertRaisesRegex(RuntimeError, "RECEIPT_MISMATCH"):
            self.invoke(self.bind(canonical), ONE)
        self.assertFalse(list(Path(self.temp.name).iterdir()))
        with self.assertRaisesRegex(PermissionError, "SOURCE"):
            self.bind(canonical, self.fixed._replace(tree="f"*40))

    def test_fixed_344_source_pair_rejects_343_and_mixed_pairs(self):
        head = "f40db673ac8d1c39d6b32146198210a09b6ef309"
        tree = "1880f68de14946f5dd66fe231ae943e2abb77598"
        old_head = "220a219aea0328a588d1053103ba17de5ecaff80"
        old_tree = "5d7a4fbd6f6136fc15f45bb8dfcd5c417922ffd1"
        self.assertEqual((head, tree), (native.OFFMENU_SOURCE, native.OFFMENU_TREE))
        for bad_head, bad_tree in ((old_head, old_tree), (head, old_tree), (old_head, tree)):
            with self.subTest(head=bad_head, tree=bad_tree):
                with self.assertRaisesRegex(PermissionError, "UNQUALIFIED_OFFMENU_SOURCE"):
                    self.bind(None, self.fixed._replace(head=bad_head, tree=bad_tree))


class NativePreparationTest(unittest.TestCase):
    def setUp(self):
        class Owner:
            head, tree = native.OFFMENU_SOURCE, native.OFFMENU_TREE
        class Publisher(Owner):
            pass
        class Acceptance(Owner):
            pass
        self.owner, self.publisher, self.acceptance = Owner(), Publisher(), Acceptance()
        self.pins = object()
        self.original = "a"*64
        self.receipt = dict(schema="offmenu.owner-local-package-receipt/1",
                            head=Owner.head, tree=Owner.tree, package_sha256="b"*64,
                            role_sha256={name: "c"*64 for name in (
                                "owner_source", "owner_venv", "owner_config",
                                "publisher", "publisher_config")})
        self.calls = []
        self.package = SimpleNamespace(
            OwnerPackageInputs=Owner, PublisherPackageInput=Publisher,
            PackageAcceptance=Acceptance, prepare_split_role_package=self.compose)

    def compose(self, operation, **inputs):
        self.calls.append((operation, inputs))
        return self.receipt

    def prepare(self, **overrides):
        inputs = dict(package=self.package, operation=self.original, owner=self.owner,
                      publisher=self.publisher, config_pins=self.pins,
                      acceptance=self.acceptance)
        return native.prepare_fixed_native_package(**dict(inputs, **overrides))

    def test_preparation_delegates_exact_original_and_typed_inputs(self):
        self.assertEqual(self.prepare(), self.receipt)
        self.assertEqual(self.calls, [(self.original, dict(
            owner=self.owner, publisher=self.publisher, config_pins=self.pins,
            acceptance=self.acceptance))])

    def test_wrong_type_original_and_source_never_compose(self):
        for role in ("owner", "publisher", "acceptance"):
            with self.subTest(role=role), self.assertRaises(TypeError):
                self.prepare(**{role: {"head": native.OFFMENU_SOURCE}})
        with self.assertRaises(ValueError):
            self.prepare(operation="not-a-pin")
        for item in (self.owner, self.publisher, self.acceptance):
            item.tree = "d"*40
            with self.assertRaisesRegex(PermissionError, "SOURCE"):
                self.prepare()
            item.tree = native.OFFMENU_TREE
        self.assertEqual(self.calls, [])

    def test_refuses_noncanonical_or_wrong_scope_receipt(self):
        base = self.receipt
        for replacement in (
                {"secret": "not allowed"}, {"head": "d"*40}, {"package_sha256": "bad"},
                {"role_sha256": {"publisher": "b"*64}}):
            with self.subTest(keys=tuple(replacement)):
                self.receipt = dict(base, **replacement)
                count = len(self.calls)
                with self.assertRaisesRegex(RuntimeError, "RECEIPT_MISMATCH"):
                    self.prepare()
                self.assertEqual(len(self.calls), count+1)

    def test_original_conflict_is_not_retried_or_claimed_complete(self):
        def already_exists(operation, **inputs):
            self.calls.append(operation)
            raise FileExistsError("retained original")
        self.package.prepare_split_role_package = already_exists
        with self.assertRaises(FileExistsError):
            self.prepare()
        self.assertEqual(self.calls, [self.original])
