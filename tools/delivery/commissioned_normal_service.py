"""Source-only renderer for the existing Stokowski unit's commissioned profile."""
import json
import re

STAGE = re.compile(r"/opt/uif-stokowski/[a-z0-9][a-z0-9.-]{1,90}")
LEGACY = {"/opt/uif-stokowski/uif1313-guarded-1f45bd56",
          "/opt/uif-stokowski/uif1313-idle-a22fbb2"}
UNIT = "stokowski-foo.service"
DROPIN = "90-uif-commissioned-normal.conf"
SELECTION = "/var/lib/uif-stokowski/commissioned-normal/selection.json"


def check_normal_stage(stage):
    if type(stage) is not str or not STAGE.fullmatch(stage) or stage in LEGACY:
        raise ValueError("Expected a distinct protected normal stage")
    return stage


def render_normal_dropin(stage):
    """Render only; an approved installer owns selection and boot enablement."""
    stage = check_normal_stage(stage)
    return (
        "[Unit]\n"
        "ConditionPathExists=\n"
        "Wants=network-online.target\nAfter=network-online.target\n"
        "StartLimitIntervalSec=300s\nStartLimitBurst=3\n\n"
        "[Service]\n"
        "Type=exec\nUser=user\nGroup=user\nSupplementaryGroups=\n"
        "NoNewPrivileges=yes\nCapabilityBoundingSet=\nAmbientCapabilities=\n"
        "NotifyAccess=main\n"
        "ExecStart=\n"
        f"ExecStart={stage}/venv/bin/python3.12 -I -S -B {stage}/launch.py\n"
        # Preserve the manual burst limit without self-latching auto-recovery:
        # even immediate repeated failures space the fourth start past 300s.
        "Restart=on-failure\nRestartSec=110s\nRestartPreventExitStatus=78\n"
        "KillSignal=SIGTERM\nTimeoutStopSec=90s\n"
    ).encode()


def render_normal_selection(stage, *, source_head, source_tree, release_sha256,
                            launcher_sha256, interpreter_sha256, authority_config_sha256):
    """Render the existing selector record, not a release acceptance or install."""
    stage = check_normal_stage(stage)
    source = {"source_head": source_head, "source_tree": source_tree}
    artifacts = {"release_sha256": release_sha256, "launcher_sha256": launcher_sha256,
                 "interpreter_sha256": interpreter_sha256,
                 "authority_config_sha256": authority_config_sha256}
    if (any(type(x) is not str or not re.fullmatch(r"[a-f0-9]{40}", x) for x in source.values())
            or any(type(x) is not str or not re.fullmatch(r"[a-f0-9]{64}", x)
                   for x in artifacts.values())):
        raise ValueError("Expected exact reviewed normal source and artifact digests")
    return json.dumps({"schema": "uif.stokowski-commissioned-normal/1", "mode": "normal",
                       "unit": UNIT, "stage": stage, **source, **artifacts},
                      sort_keys=True, separators=(",", ":")).encode()


def normal_entry_pins(stage, source_head, source_tree):
    """Render a self-contained, stdlib-only block before staged imports."""
    stage = check_normal_stage(stage)
    if any(type(pin) is not str or not re.fullmatch(r"[a-f0-9]{40}", pin)
           for pin in (source_head, source_tree)):
        raise ValueError("Expected exact reviewed normal source head and tree")
    return f"""    import hashlib as _CHash, json as _CJson, os as _COs, pwd as _CPwd, stat as _CStat, sys as _CSys
    def _CDuplicate(pairs):
        result = {{}}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate commissioned record key")
            result[key] = value
        return result
    def _CRaw(path, maximum):
        parts = path.parts
        fd = _COs.open("/", _COs.O_RDONLY | _COs.O_DIRECTORY)
        try:
            for part in parts[1:-1]:
                child = _COs.open(part, _COs.O_RDONLY | _COs.O_DIRECTORY | _COs.O_NOFOLLOW, dir_fd=fd)
                _COs.close(fd)
                fd = child
                info = _COs.fstat(fd)
                if info.st_uid != 0 or info.st_mode & 0o022:
                    raise ValueError("unprotected commissioned parent")
            leaf = _COs.open(parts[-1], _COs.O_RDONLY | _COs.O_NOFOLLOW | _COs.O_NONBLOCK, dir_fd=fd)
            with _COs.fdopen(leaf, "rb") as stream:
                info = _COs.fstat(stream.fileno())
                if (not _CStat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
                        or info.st_mode & 0o022 or not 0 < info.st_size <= maximum):
                    raise ValueError("unprotected commissioned member")
                raw = stream.read(maximum + 1)
                after = _COs.fstat(stream.fileno())
                if (len(raw), after.st_mtime_ns, after.st_ctime_ns) != (info.st_size, info.st_mtime_ns, info.st_ctime_ns):
                    raise ValueError("commissioned member changed")
                return raw
        finally:
            _COs.close(fd)
    def _VerifyCommissionedSelection():
        try:
            _CService = _CPwd.getpwnam("user")
            if (_CSys.argv[1:] or not (_CSys.flags.isolated and _CSys.flags.no_site and _CSys.dont_write_bytecode)
                    or _CService.pw_uid == 0 or _COs.getresuid() != (_CService.pw_uid,) * 3
                    or _COs.getresgid() != (_CService.pw_gid,) * 3):
                raise ValueError("commissioned process context")
            _CStage = _GuardedPath({stage!r})
            _CLauncher = _CStage / "launch.py"
            _CInterpreter = _CStage / "venv/bin/python3.12"
            _CConfig = _CStage / "selector-v1/remote-authority.json"
            if _GuardedPath(__file__) != _CLauncher or _GuardedPath(_CSys.executable) != _CInterpreter:
                raise ValueError("commissioned process path")
            _CRec = _CJson.loads(_CRaw(_GuardedPath({SELECTION!r}), 16384), object_pairs_hook=_CDuplicate)
            _CFields = {{"schema", "mode", "unit", "stage", "source_head", "source_tree",
                        "release_sha256", "launcher_sha256", "interpreter_sha256", "authority_config_sha256"}}
            if (type(_CRec) is not dict or _CRec.keys() != _CFields
                    or (_CRec["schema"], _CRec["mode"], _CRec["unit"], _CRec["stage"],
                        _CRec["source_head"], _CRec["source_tree"]) !=
                       ("uif.stokowski-commissioned-normal/1", "normal", {UNIT!r}, {stage!r}, {source_head!r}, {source_tree!r})):
                raise ValueError("commissioned selection mismatch")
            for _CPin in ("release_sha256", "launcher_sha256", "interpreter_sha256", "authority_config_sha256"):
                _CValue = _CRec[_CPin]
                if type(_CValue) is not str or len(_CValue) != 64 or any(c not in "0123456789abcdef" for c in _CValue):
                    raise ValueError("commissioned digest wrong")
            for _CPath, _CPin, _CLimit in ((_CLauncher, "launcher_sha256", 4*1024*1024),
                                          (_CInterpreter, "interpreter_sha256", 128*1024*1024),
                                          (_CConfig, "authority_config_sha256", 4*1024*1024)):
                _CData = _CRaw(_CPath, _CLimit)
                if _CHash.sha256(_CData).hexdigest() != _CRec[_CPin]:
                    raise ValueError("commissioned member digest mismatch")
            if _CJson.loads(_CData, object_pairs_hook=_CDuplicate).get("schema") != "uif.stokowski-remote-authority/1":
                raise ValueError("commissioned normal authority configuration required")
            # release_sha256 is the independently reviewed installed closure
            # identity; it is not derived from the config or a fresh CLI request.
        except (OSError, UnicodeError, ValueError, AttributeError, TypeError, KeyError):
            print("Stokowski commissioned normal selection is not verified.", file=_CSys.stderr)
            raise SystemExit(78) from None
    _VerifyCommissionedSelection()
"""
