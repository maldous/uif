"""Private prepare-only initialization for one OffMenu owner packet.

The mounted owner process authenticates role snapshots and review first. No
record received from a Windmill request or produced here constitutes authority.
"""
import fcntl
from contextlib import ExitStack
from hashlib import sha256
import os
from pathlib import Path
import stat

from offmenu_native_release import (
    OFFMENU_SOURCE, OFFMENU_TREE, SHA256, prepare_fixed_native_package,
)
from offmenu_verifier_v2_loader import LoadedOffMenuV2
from verifier_shelf_provision import protected_directory


def _original_release_effects_absent(modules, operation):
    """Read exact existing same-original fences; ambiguity is not absence."""
    ingress = modules.ingress
    def absent(directory, name):
        try:
            os.stat(name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            return True
        return False
    try:
        with ExitStack() as stack:
            def hold(fd):
                stack.callback(os.close, fd)
                return fd
            fence = hold(protected_directory(ingress.publisher_fence(), True))
            canonical = hold(protected_directory(Path(modules.upgrade.STATE), True))
            return absent(fence, operation) and all(absent(canonical, name) for name in (
                f"native-package-{operation}.intent.json",
                f"native-upgrade-{operation}.intent.json",
                f"native-upgrade-{operation}.installed.json"))
    except (OSError, RuntimeError, ValueError, KeyError, TypeError):
        return False


def _qualified_review(ingress, review_raw, review_sha256, owner_principal, source):
    """Same installed review schema, exact raw digest and independent roles."""
    if (type(review_raw) is not bytes or not 0 < len(review_raw) <= ingress.SMALL
            or (type(review_sha256) is not str or not SHA256.fullmatch(review_sha256)) or sha256(review_raw).hexdigest() != review_sha256):
        raise ValueError("OFFMENU_OWNER_REVIEW_BYTES_MISMATCH")
    review = ingress.shape(ingress.record(review_raw), "schema", "owner", "head", "tree",
                           "author", "reviewer", "verdict")
    if ((review["schema"], review["owner"], review["verdict"]) !=
               ("offmenu.native-package-review/1", owner_principal, "accept")
            or (review["head"], review["tree"]) != source
            or any(type(value) is not str or not ingress.OWNER.fullmatch(value)
                   for value in (owner_principal, review["author"], review["reviewer"]))
            or review["reviewer"].casefold() in
               (owner_principal.casefold(), review["author"].casefold())):
        raise PermissionError("OFFMENU_INDEPENDENT_REVIEW_REQUIRED")


def initialize_offmenu_owner_packet(modules, operation, *, owner_principal, review_raw,
                                    review_sha256, owner, publisher, config_pins):
    """Retain exact existing review and prepare the same fresh original once.

    Only the trusted owner calls this with the verified private loader
    context still open, never a trigger, external request or install action.
    If any effect is ambiguous, reconcile the retained packet read-only.
    """
    if os.getresuid() != (0, 0, 0) or type(modules) is not LoadedOffMenuV2:
        raise PermissionError("OFFMENU_PROTECTED_OWNER_CONTEXT_REQUIRED")
    if type(operation) is not str or not SHA256.fullmatch(operation):
        raise ValueError("INVALID_OFFMENU_PREPARATION_ORIGINAL")
    ingress, package = modules.ingress, modules.package
    if (type(owner) is not package.OwnerPackageInputs
            or type(publisher) is not package.PublisherPackageInput):
        raise TypeError("INVALID_OFFMENU_PREPARATION_INPUTS")
    source = (OFFMENU_SOURCE, OFFMENU_TREE)
    if any((item.head, item.tree) != source for item in (owner, publisher)):
        raise PermissionError("UNQUALIFIED_OFFMENU_SOURCE")

    def pin(value):
        return type(value) is str and SHA256.fullmatch(value)

    _qualified_review(ingress, review_raw, review_sha256, owner_principal, source)
    if (not pin(owner.interpreter_sha256) or not pin(owner.node_sha256)
            or type(config_pins) is not dict
            or set(config_pins) != {"owner_current", "owner_catalog", "publisher_config"}
            or not all(map(pin, config_pins.values()))):
        raise ValueError("OFFMENU_CUSTODY_PINS_REQUIRED")
    for ref in (owner.interpreter_custody, owner.node_custody):
        if type(ref) is not dict or set(ref) != {"path", "sha256"} or not pin(ref["sha256"]):
            raise ValueError("OFFMENU_CUSTODY_PINS_REQUIRED")
        package.path_name(ref["path"])

    # The roots agree in the reviewed modules. Existing roots and originals
    # are never created here, accepted as a retry, or silently overwritten.
    if ingress.STATE != package._SPLIT_PACKET_ROOT:
        raise RuntimeError("OFFMENU_OWNER_PACKET_ROOT_MISMATCH")
    if not _original_release_effects_absent(modules, operation):
        raise RuntimeError("OFFMENU_PREPARATION_ORIGINAL_REQUIRES_RECONCILIATION")
    fd = protected_directory(ingress.STATE, True)
    try:
        os.mkdir(operation, mode=0o700, dir_fd=fd)
        os.fsync(fd)
        packet = os.open(operation, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                         dir_fd=fd)
        try:
            info = os.fstat(packet)
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
                raise PermissionError("OFFMENU_OWNER_PACKET_CUSTODY")
            fcntl.flock(packet, fcntl.LOCK_EX | fcntl.LOCK_NB)
            leaf = os.open("package-review.json",
                           os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                           0o600, dir_fd=packet)
            try:
                pending = memoryview(review_raw)
                while pending:
                    count = os.write(leaf, pending)
                    if count <= 0:
                        raise OSError("OFFMENU_OWNER_REVIEW_WRITE_INCOMPLETE")
                    pending = pending[count:]
                os.fsync(leaf)
            finally:
                os.close(leaf)
            os.fsync(packet)
            acceptance = package.PackageAcceptance(owner_principal, *source,
                {"path": "package-review.json", "sha256": review_sha256})
            return prepare_fixed_native_package(package, operation, owner=owner,
                publisher=publisher, config_pins=config_pins, acceptance=acceptance)
        finally:
            os.close(packet)
    finally:
        os.close(fd)


def observe_offmenu_owner_packet(modules, operation, *, acceptance,
                                 package_sha256, config_pins,
                                 interpreter_custody, node_custody):
    """Observe only the same existing original; never run the composer again.

    Acceptance and expected digest/references are from the protected caller.
    Missing independent package pin or uncertain disk state remains reconcile.
    A prepared result is a package receipt only, never installation authority.
    """
    if os.getresuid() != (0, 0, 0) or type(modules) is not LoadedOffMenuV2:
        raise PermissionError("OFFMENU_PROTECTED_OWNER_CONTEXT_REQUIRED")
    if type(operation) is not str or not SHA256.fullmatch(operation):
        raise ValueError("INVALID_OFFMENU_PREPARATION_ORIGINAL")
    ingress, package = modules.ingress, modules.package
    source = (OFFMENU_SOURCE, OFFMENU_TREE)
    if (type(acceptance) is not package.PackageAcceptance
            or (acceptance.head, acceptance.tree) != source):
        raise PermissionError("UNQUALIFIED_OFFMENU_SOURCE")
    if ingress.STATE != package._SPLIT_PACKET_ROOT:
        raise RuntimeError("OFFMENU_OWNER_PACKET_ROOT_MISMATCH")
    review = acceptance.review
    def pinned(value):
        return type(value) is str and SHA256.fullmatch(value)
    if (type(review) is not dict or set(review) != {"path", "sha256"}
            or review["path"] != "package-review.json" or not pinned(review["sha256"])
            or (package_sha256 is not None and not pinned(package_sha256))
            or type(config_pins) is not dict
            or set(config_pins) != {"owner_current", "owner_catalog", "publisher_config"}
            or not all(map(pinned, config_pins.values()))):
        raise ValueError("OFFMENU_PREPARATION_READBACK_PINS_REQUIRED")
    for ref in (interpreter_custody, node_custody):
        if type(ref) is not dict or set(ref) != {"path", "sha256"} or not pinned(ref["sha256"]):
            raise ValueError("OFFMENU_CUSTODY_PINS_REQUIRED")
        package.path_name(ref["path"])

    ambiguous = {"state": "reconcile"}
    if not _original_release_effects_absent(modules, operation):
        return ambiguous
    try:
        fd = protected_directory(ingress.STATE, True)
    except (OSError, ValueError):
        return ambiguous
    try:
        try:
            packet_fd = os.open(operation, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=fd)
        except FileNotFoundError:
            return {"state": "not_started"}
        except OSError:
            return ambiguous
        try:
            info = os.fstat(packet_fd)
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
                return ambiguous
            if package_sha256 is None:
                return ambiguous
            packet = ingress.STATE / operation
            exact_review = ingress.read(packet / "package-review.json", ingress.SMALL)
            _qualified_review(ingress, exact_review, review["sha256"], acceptance.owner, source)
            pkg_info = os.stat("package", dir_fd=packet_fd, follow_symlinks=False)
            if not stat.S_ISDIR(pkg_info.st_mode) or pkg_info.st_uid != 0 or pkg_info.st_mode & 0o077:
                return ambiguous
            manifest, _ = package.read_package(packet / "package", package_sha256)
            if ((manifest["head"], manifest["tree"]) != source
                    or manifest["owner"] != acceptance.owner or manifest["review"] != review
                    or manifest["interpreter_custody"] != interpreter_custody
                    or manifest["node_custody"] != node_custody):
                return ambiguous
            roles = manifest["roles"]
            if (roles["owner_config"]["files"]["current.json"]["sha256"] != config_pins["owner_current"]
                    or roles["owner_config"]["files"]["catalog.json"]["sha256"] != config_pins["owner_catalog"]
                    or roles["publisher_config"]["files"]["config.json"]["sha256"] != config_pins["publisher_config"]):
                return ambiguous
            after = os.stat(operation, dir_fd=fd, follow_symlinks=False)
            pkg_after = os.stat("package", dir_fd=packet_fd, follow_symlinks=False)
            def identity(value):
                return value.st_dev, value.st_ino, value.st_uid, value.st_mode
            if identity(info) != identity(after) or identity(pkg_info) != identity(pkg_after):
                return ambiguous
            return {"state": "prepared", "receipt": package._owner_local_receipt(
                {"sha256": package_sha256, "roles": roles}, acceptance)}
        except (OSError, RuntimeError, ValueError, KeyError, TypeError):
            return ambiguous
        finally:
            os.close(packet_fd)
    finally:
        os.close(fd)


def resume_unstarted_offmenu_owner_package(modules, operation, *, acceptance,
                                            owner, publisher, config_pins):
    """Finish a proven review-only original, never reinitialize or replay.

    The protected sole writer supplies the same authenticated acceptance and
    exact role inputs, not a request-derived context. An absent/partial
    review, a package of any kind or any release effect remains reconcile.
    """
    if os.getresuid() != (0, 0, 0) or type(modules) is not LoadedOffMenuV2:
        raise PermissionError("OFFMENU_PROTECTED_OWNER_CONTEXT_REQUIRED")
    if type(operation) is not str or not SHA256.fullmatch(operation):
        raise ValueError("INVALID_OFFMENU_PREPARATION_ORIGINAL")
    ingress, package = modules.ingress, modules.package
    source = (OFFMENU_SOURCE, OFFMENU_TREE)
    if (type(acceptance) is not package.PackageAcceptance
            or type(owner) is not package.OwnerPackageInputs
            or type(publisher) is not package.PublisherPackageInput
            or any((obj.head, obj.tree) != source for obj in (acceptance, owner, publisher))):
        raise PermissionError("UNQUALIFIED_OFFMENU_SOURCE")
    review = acceptance.review
    if (type(review) is not dict or set(review) != {"path", "sha256"}
            or review["path"] != "package-review.json"
            or type(review["sha256"]) is not str or not SHA256.fullmatch(review["sha256"])):
        raise ValueError("OFFMENU_PREPARATION_READBACK_PINS_REQUIRED")
    if ingress.STATE != package._SPLIT_PACKET_ROOT:
        raise RuntimeError("OFFMENU_OWNER_PACKET_ROOT_MISMATCH")

    ambiguous = {"state": "reconcile"}
    # Existing release journal takes the same packet inode flock.
    try:
        with ExitStack() as stack:
            def hold(fd):
                stack.callback(os.close, fd)
                return fd
            fd = hold(protected_directory(ingress.STATE, True))
            packet = hold(os.open(operation, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                  dir_fd=fd))
            info = os.fstat(packet)
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
                return ambiguous
            fcntl.flock(packet, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if set(os.listdir(packet)) != {"package-review.json"}:
                return ambiguous
            if not _original_release_effects_absent(modules, operation):
                return ambiguous
            review_fd = hold(os.open("package-review.json",
                                     os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                     dir_fd=packet))
            st = os.fstat(review_fd)
            if (not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or st.st_nlink != 1
                    or st.st_mode & 0o077 or not 0 < st.st_size <= ingress.SMALL):
                return ambiguous
            with os.fdopen(review_fd, "rb", closefd=False) as stream:
                raw = stream.read(ingress.SMALL + 1)
            if len(raw) != st.st_size:
                return ambiguous
            _qualified_review(ingress, raw, review["sha256"], acceptance.owner, source)
            current = os.stat(operation, dir_fd=fd, follow_symlinks=False)
            if ((current.st_dev, current.st_ino, current.st_uid, current.st_mode)
                    != (info.st_dev, info.st_ino, info.st_uid, info.st_mode)):
                return ambiguous
            # Same exact retained bytes; consolidate a potentially interrupted
            # sync before package creation, never rewrite the existing review.
            os.fsync(review_fd)
            os.fsync(packet)
            if set(os.listdir(packet)) != {"package-review.json"}:
                return ambiguous
            return {"state": "prepared", "receipt": prepare_fixed_native_package(
                package, operation, owner=owner, publisher=publisher,
                config_pins=config_pins, acceptance=acceptance)}
    except (OSError, RuntimeError, ValueError, KeyError, TypeError):
        return ambiguous
