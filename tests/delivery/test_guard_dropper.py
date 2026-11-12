"""Compile UIF confinement source and test its validators without elevated launch."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
GUARD = ROOT / "tools/delivery/uif_coding_child_guard.c"
DROPPER = ROOT / "deploy/stokowski/uif-coding-dropper.sh"
CC = shutil.which("cc")
DESCRIPTOR_SHA = "e7774528834d5232ece821e6f2d23d91b5cd6b9ec6a8d094f4e0f88b6d933069"
CFLAGS = ["-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-fPIE", "-pie"]

PROBE = r'''
#define main uif_guard_main
#include UIF_GUARD_SOURCE
#undef main

static int fixture_dir(const char *path)
{
    if (!absolute_path(path) || !under(path, UIF_RUNTIME_ROOT))
        refuse();
    int fd = open(UIF_RUNTIME_ROOT, O_PATH | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    validate_dir(fd, UIF_UID);
    return descend_role_slot(fd, path + strlen(UIF_RUNTIME_ROOT) + 1, UIF_UID);
}

int main(int argc, char **argv)
{
    if (argc < 2)
        return 2;
    if (!strcmp(argv[1], "pins")) {
        check_pins();
    } else if (!strcmp(argv[1], "paths") && argc == 7) {
        for (int i = 3; i < argc; i++)
            if (!absolute_path(argv[i]) || !under(argv[i], UIF_RUNTIME_ROOT))
                refuse();
        check_attempt(argv[2], argv[3], argv[4], argv[5], argv[6]);
    } else if (!strcmp(argv[1], "dir") && argc == 3) {
        close(fixture_dir(argv[2]));
    } else if (!strcmp(argv[1], "auth") && argc == 3) {
        int home = fixture_dir(argv[2]);
        check_auth(home);
        close(home);
    } else if (!strcmp(argv[1], "output") && argc == 3) {
        int dir = fixture_dir(argv[2]);
        require_absent(dir, "final-message.txt");
        require_absent(dir, "codex-state");
        close(dir);
    } else if (!strcmp(argv[1], "native")) {
        check_pins();
        close(native_fd());
    } else if (!strcmp(argv[1], "native-bytes") && argc == 3) {
        check_pins();
        int fd = open(argv[2], O_RDONLY | O_NOFOLLOW);
        if (fd < 0)
            return 6;
        verify_native_bytes(fd);
        close(fd);
    } else if (!strcmp(argv[1], "descriptors") && argc == 3) {
        int inherited = atoi(argv[2]);
        if (inherited < 3 || fcntl(inherited, F_GETFD) < 0)
            return 4;
        descriptors();
        if (fcntl(inherited, F_GETFD) != -1 || errno != EBADF)
            return 5;
    } else {
        return 3;
    }
    return 0;
}
'''


@unittest.skipUnless(CC and os.uname().sysname == "Linux" and os.uname().machine == "x86_64"
                     and os.getuid() != 0 and os.getgid() != 0,
                     "requires an unprivileged Linux x86_64 C toolchain")
class GuardTests(unittest.TestCase):
    def setUp(self):
        self.scratch = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.root = self.scratch / "uif-runtime"
        self.root.mkdir(mode=0o700)
        self.runtime = self.root / "job"
        self.workspace = self.runtime / "workspaces" / "UIF-41"
        self.attempt = self.runtime / "attempts" / "UIF-41"
        self.home = self.attempt / "codex-home-fixture"
        self.auth = self.scratch / "fixture-auth-reference"
        self.auth.touch(mode=0o600)
        self.review = self.runtime / "attempts" / "review-UIF-41-stage-x"
        self.candidate = self.root / "separate" / "contributions" / "UIF-41" / "candidate"
        for directory in (self.workspace, self.home, self.review, self.candidate):
            directory.mkdir(mode=0o700, parents=True)
        for directory in self.root.rglob("*"):
            if directory.is_dir():
                directory.chmod(0o700)
        (self.home / "auth.json").symlink_to(self.auth)
        self.native = self.scratch / "fixture-native"
        self.native.write_bytes(b"disposable executable hash input")
        self.native_sha = hashlib.sha256(self.native.read_bytes()).hexdigest()
        self.source = self.scratch / "probe.c"
        self.source.write_text(PROBE)
        self.probe = self.compile("probe", self.source)

    def bindings(self, **changes):
        return dict({
            "UIF_UID": os.getuid(),
            "UIF_GID": os.getgid(),
            "UIF_PRINCIPAL": "fixture",
            "UIF_ROLE_MODE": "workspace-write",
            "UIF_HOME": str(self.scratch),
            "UIF_RUNTIME_ROOT": str(self.root),
            "UIF_WORK_ROOT": str(self.root),
            "UIF_REVIEW_ROOT": str(self.root),
            "UIF_OUTPUT_ROOT": str(self.root),
            "UIF_AUTH_REFERENCE": str(self.auth),
            "UIF_NATIVE": str(self.native),
            "UIF_NATIVE_SHA256": self.native_sha,
            "UIF_DESCRIPTOR_SHA256": DESCRIPTOR_SHA,
            "UIF_GUARD_SOURCE": str(GUARD),
        }, **changes)

    def compile(self, name, source, **changes):
        header = self.scratch / (name + ".h")
        header.write_text("".join(f"#define {key} {json.dumps(value)}\n"
                                  for key, value in self.bindings(**changes).items()))
        binary = self.scratch / name
        subprocess.run([CC, *CFLAGS, "-include", str(header), str(source), "-o", str(binary)],
                       check=True, capture_output=True)
        return binary

    def invoke(self, *args, executable=None, expected=0):
        result = subprocess.run([str(executable or self.probe), *map(str, args)],
                                check=False, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, expected, result.stderr)
        if expected == 126:
            self.assertEqual(result.stderr, b"UIF coding confinement refused\n")

    def paths(self, mode, workspace=None, attempt=None, home=None, expected=0):
        attempt = attempt or self.attempt
        self.invoke("paths", mode, workspace or self.workspace, attempt / "final-message.txt",
                    home or attempt / "codex-home-fixture", attempt / "codex-state", expected=expected)

    def test_requires_explicit_binding_and_post_drop_context(self):
        unconfigured = subprocess.run([CC, *CFLAGS, "-fsyntax-only", str(GUARD)],
                                      capture_output=True, timeout=15)
        self.assertNotEqual(unconfigured.returncode, 0)
        self.assertIn(b"explicitly qualified binding header", unconfigured.stderr)
        # A known different UID proves refusal before any input path is inspected.
        binary = self.compile("wrong-context", GUARD, UIF_UID=os.getuid() + 1)
        self.invoke("workspace-write", self.workspace, self.attempt / "final-message.txt",
                    self.home, self.attempt / "codex-state", executable=binary, expected=126)

    def test_exact_issue_runtime_mode_and_output_binding(self):
        self.paths("workspace-write")
        self.paths("read-only", attempt=self.review)
        self.paths("read-only", workspace=self.candidate, attempt=self.review)
        self.paths("workspace-write", workspace=self.candidate, expected=126)
        self.paths("workspace-write", attempt=self.review, expected=126)
        self.paths("read-only", expected=126)
        self.paths("full-access", expected=126)
        self.paths("workspace-write", workspace=self.workspace.with_name("UIF-42"), expected=126)
        self.paths("read-only", attempt=self.review.with_name("review-UIF-410"), expected=126)
        self.paths("workspace-write", workspace=self.root / "other/workspaces/UIF-41", expected=126)
        self.paths("workspace-write", home=self.review / "codex-home-fixture", expected=126)
        self.paths("workspace-write", workspace=self.root / "job/../job/workspaces/UIF-41", expected=126)

    def test_directory_custody_and_attempt_preservation(self):
        self.invoke("dir", self.workspace)
        self.invoke("output", self.attempt)
        (self.attempt / "final-message.txt").touch()
        self.invoke("output", self.attempt, expected=126)
        self.workspace.chmod(0o770)
        self.invoke("dir", self.workspace, expected=126)
        self.workspace.chmod(0o700)
        alias = self.workspace.parent / "alias"
        alias.symlink_to(self.workspace, target_is_directory=True)
        self.invoke("dir", alias, expected=126)

    def test_auth_checks_reference_and_metadata(self):
        self.invoke("auth", self.home)
        self.auth.chmod(0o644)
        self.invoke("auth", self.home, expected=126)
        self.auth.chmod(0o600)
        (self.home / "auth.json").unlink()
        (self.home / "auth.json").symlink_to(self.scratch / "unapproved")
        self.invoke("auth", self.home, expected=126)

    def test_descriptor_policy_and_native_pin(self):
        self.invoke("pins")
        bad = self.compile("bad-descriptor", self.source, UIF_DESCRIPTOR_SHA256="0" * 64)
        self.invoke("pins", executable=bad, expected=126)
        self.invoke("native", expected=126)  # User-owned fixture is not an installed executable.
        self.invoke("native-bytes", self.native)
        mismatch = self.compile("bad-native", self.source, UIF_NATIVE_SHA256="0" * 64)
        self.invoke("native-bytes", self.native, executable=mismatch, expected=126)

    def test_descriptor_closure_and_socket_refusal(self):
        extra = os.open(self.auth, os.O_PATH)
        try:
            command = [str(self.probe), "descriptors", str(extra)]
            stdout_path = self.scratch / "stdout"
            stderr_path = self.scratch / "stderr"
            stdout_path.touch(mode=0o600)
            stderr_path.touch(mode=0o600)
            with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
                result = subprocess.run(command, input=b"", stdout=out, stderr=err,
                                        pass_fds=(extra,), timeout=5)
            self.assertEqual(result.returncode, 0, stderr_path.read_bytes())
            left, right = socket.socketpair()
            with left, right, stdout_path.open("wb") as out, stderr_path.open("wb") as err:
                result = subprocess.run(command, stdin=left, stdout=out, stderr=err,
                                        pass_fds=(extra,), timeout=5)
            self.assertEqual(result.returncode, 126)
        finally:
            os.close(extra)


class DropperTests(unittest.TestCase):
    def test_unrendered_template_refuses_without_starting_dropper(self):
        subprocess.run(["/bin/sh", "-n", str(DROPPER)], check=True, timeout=5)
        result = subprocess.run(["/bin/sh", "-x", str(DROPPER), "read-only"],
                                capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 126)
        self.assertNotIn(b"/usr/bin/setpriv", result.stderr)
