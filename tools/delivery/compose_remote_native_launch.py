#!/usr/bin/env python3
"""Compose the verified parked native launcher in an offline staging directory.

Root installs the resulting bytes through the existing service rollout. This
tool never reads a credential, starts a service, creates an activation marker,
or changes an existing installation.
"""
import argparse
from hashlib import sha256
from pathlib import Path
import re


PREIMAGE = 'b3ef2f6e6a0bc3b3370abb0e582f2e1794c774a8402ae4851c343e7e9a65f084'
OLD_STAGE = '/opt/uif-stokowski/uif1313-guarded-1f45bd56'
OLD_LAUNCHER = '970929f77d4e24bc20fcba2864a4435b3a7791843eedc4874f4f3e66c4fb1aed'
ORIGINAL_MAIN = '''if __name__ == "__main__":
    guarded_staged_entry()'''
ORIGINAL_CONFIG = '''    configure(runner, orchestrator, publication,
              policy=Path('''
EXACT_LEGACY_MARKER = '''    if not _GuardedMarker.is_file():
        raise SystemExit("Stokowski activation is held; installed binding is parked.")'''
ORIGINAL_GUARD = '''    if not _guarded_startup_authorized(startup_binding, startup_verifier):
        raise SystemExit("Stokowski normal startup is held; owner-scoped shared authority is unavailable.")'''


def compose(source, stage, launcher_pin, composition_pin, transport_pin, source_pin, native_pin, turn_pin,
            *, commissioned_source=None):
    if sha256(source).hexdigest() != PREIMAGE:
        raise ValueError('Unrecognized parked launcher bytes')
    if not re.fullmatch(r'/opt/uif-stokowski/[a-z0-9][a-z0-9.-]{1,90}', stage) or stage == OLD_STAGE:
        raise ValueError('Expected an isolated absolute rollout stage')
    if not all(re.fullmatch('[a-f0-9]{64}', pin) for pin in (launcher_pin, composition_pin, transport_pin, source_pin, native_pin, turn_pin)):
        raise ValueError('Expected reviewed package member digests')
    src = source.decode().replace(OLD_STAGE, stage)
    verifier = 'def _guarded_startup_authorized(binding, verify):'
    entry = 'def guarded_staged_entry(*, startup_binding=None, startup_verifier=None):'
    receipt_state = "or observed_binding != binding or type(state) is not str or state != 'mounted'"
    for needle in (OLD_LAUNCHER, ORIGINAL_MAIN, ORIGINAL_CONFIG, ORIGINAL_GUARD,
                   verifier, entry, receipt_state):
        if src.count(needle) != 1:
            raise ValueError('Unrecognized guarded launcher composition')
    src = src.replace(OLD_LAUNCHER, launcher_pin)
    src = src.replace(ORIGINAL_CONFIG, '''    _NativeSinks(orchestrator, _RemoteComposition)
    configure(runner, orchestrator, publication, **_RemoteComposition.options(),
              policy=Path(''')
    src = src.replace(verifier, 'def _guarded_startup_authorized(binding, verify, expected_state):')
    src = src.replace(receipt_state, receipt_state.replace("'mounted'", 'expected_state'))
    src = src.replace(entry, entry.replace('startup_verifier=None', 'startup_verifier=None, idle_reason=None'))
    legacy_guard = '''    expected_state = 'stopped_idle' if idle_reason is not None else 'mounted'
    if idle_reason is not None:
        import stat
        marker = _GuardedMarker.lstat()
        if not stat.S_ISREG(marker.st_mode) or marker.st_uid != 0 or marker.st_mode & 0o022:
            raise SystemExit("Stokowski idle startup requires a protected activation marker.")
    if not _guarded_startup_authorized(startup_binding, startup_verifier, expected_state):
        mode = 'idle' if idle_reason is not None else 'normal'
        raise SystemExit(f"Stokowski {mode} startup is held; owner-scoped shared authority is unavailable.")
    if idle_reason is not None:
        from uif.stokowski_cloudflare.launcher import serve_supervised_idle
        return serve_supervised_idle(idle_reason)'''
    native_source_check = ''
    if commissioned_source is None:
        guard = legacy_guard
        stage_argument = "_GuardedMarker.parent.name"
        adapter_check = '''        if _Member.resolve() != _Member or _StartupHash.sha256(_Member.read_bytes()).hexdigest() != _Pin:
            raise SystemExit("Installed native authority adapter differs from the reviewed pin")'''
        entry_pins = '''    if any(arg.partition("=")[0] == _GuardedAdminFlag for arg in _GuardedArgv[1:]) or not _GuardedMarker.is_file():
        guarded_staged_entry()
'''
    else:
        from commissioned_normal_service import normal_entry_pins
        entry_pins = normal_entry_pins(stage, *commissioned_source)
        src = src.replace('startup_verifier=None, idle_reason=None):',
                          'startup_verifier=None, idle_reason=None, commissioned_no_route=False):')
        # Keep the existing stdin credential and operating directory. Only the
        # guarded-idle launcher resolves code from the historical native shelf.
        for legacy_path in ('    ROOT=Path("/opt/uif-stokowski/native-core-20261113-a1")\n',
                            '    sys.path.insert(0,str(ROOT/"python"))\n',
                            '    sys.path.append(str(ROOT/"web-deps"))\n'):
            if src.count(legacy_path) != 1:
                raise ValueError('Unrecognized native shelf imports in parked launcher')
            src = src.replace(legacy_path, '')
        native_source_check = '''    try:
        for _Source in ("__init__", "__main__", "config", "runner", "orchestrator", "publication"):
            _CRaw(_Site / "stokowski" / (_Source + ".py"), 4*1024*1024)
    except (OSError, ValueError):
        print("Stokowski commissioned native source is unavailable.", file=_StartupSys.stderr)
        raise SystemExit(78) from None
'''
        if src.count(EXACT_LEGACY_MARKER) != 2:
            raise ValueError('Unrecognized guarded legacy marker sites')
        src = src.replace(EXACT_LEGACY_MARKER, '    _VerifyCommissionedSelection()')
        src = src.replace('# The final marker metadata read can itself block past the deadline.',
                          '# The final commissioned selection read can itself block past the deadline.')
        stage_argument = repr(Path(stage).name)
        adapter_check = '''        try:
            if _StartupHash.sha256(_CRaw(_Member, 4*1024*1024)).hexdigest() != _Pin:
                raise ValueError("installed adapter pin")
        except (OSError, ValueError):
            print("Stokowski commissioned normal adapter is not verified.", file=_StartupSys.stderr)
            raise SystemExit(78) from None'''
        guard = '''    if (type(commissioned_no_route) is not bool or idle_reason is not None
            or (_RemoteComposition is None) != commissioned_no_route):
        raise SystemExit(78)
    import sys
    from uif.handoff_store import Hold
    from uif.stokowski_cloudflare.launcher import wait_for_normal_authority
    from uif.stokowski_cloudflare.remote_authority import AuthorityUnavailable

    def _commissioned_verify():
        original_error = None

        def observe(request):
            nonlocal original_error
            try:
                return startup_verifier(request)
            except Exception as error:
                original_error = error
                raise

        try:
            authorized = _guarded_startup_authorized(startup_binding, observe, 'mounted')
        except Exception as error:
            if error is not original_error:
                raise
            authorized = False
        if original_error is None:
            return authorized is True
        if isinstance(original_error, AuthorityUnavailable):
            return False
        if isinstance(original_error, Hold) and len(original_error.args) == 1:
            reason = original_error.args[0]
            if type(reason) is str and (
                    reason in ('authority_start_expired', 'authority_http_401', 'authority_http_403',
                               'authority_http_408', 'authority_http_409', 'authority_http_429')
                    or reason.startswith('authority_http_5') and len(reason) == 18
                    and reason[-3:].isdigit()):
                return False
        print("Stokowski commissioned normal authority configuration is not verified.",
              file=sys.stderr)
        raise SystemExit(78) from None

    wait_for_normal_authority(_commissioned_verify)
    if commissioned_no_route:
        from uif.stokowski_cloudflare.launcher import serve_commissioned_no_route_idle
        return serve_commissioned_no_route_idle(_commissioned_verify)'''
    src = src.replace(ORIGINAL_GUARD, guard)
    # Isolated mode (-I -S -B) remains mandatory even before importing the adapter.
    # The installed package tree and interpreter are independently pinned by the
    # service installer; verify new trusted adapter members prior to import.
    return src.replace(ORIGINAL_MAIN, f'''if __name__ == "__main__":
{entry_pins}    if not (_GuardedFlags.isolated and _GuardedFlags.no_site and _GuardedFlags.dont_write_bytecode):
        raise SystemExit("Native authority requires the isolated installed interpreter")
    import hashlib as _StartupHash, sys as _StartupSys
    _Site = _GuardedPath({stage!r}) / "venv/lib/python3.12/site-packages"
{native_source_check}    for _Name, _Pin in (("native_composition", {composition_pin!r}), ("remote_authority", {transport_pin!r}),
                         ("remote_source", {source_pin!r}), ("native", {native_pin!r}),
                         ("turn", {turn_pin!r}), ("launcher", {launcher_pin!r})):
        _Member = _Site / "uif/stokowski_cloudflare" / (_Name + ".py")
{adapter_check}
    _StartupSys.path.insert(0, str(_Site))
    from uif.stokowski_cloudflare.native_composition import load_guarded_startup as _Load, install_sinks as _NativeSinks
    _RemoteComposition, _StartupArgs = _Load(
        _GuardedPath({stage!r}) / "selector-v1/remote-authority.json",
        _GuardedStartUnit, {stage_argument},
    )
    guarded_staged_entry(**_StartupArgs)''').encode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ('fixture', 'stage', 'launcher_pin', 'composition_pin', 'transport_pin', 'source_pin', 'native_pin', 'turn_pin', 'output'):
        parser.add_argument(arg)
    parser.add_argument('--commissioned-source', nargs=2, metavar=('HEAD', 'TREE'),
                        help='Select the distinct, approved persistent normal release renderer')
    args = parser.parse_args()
    rendered = compose(Path(args.fixture).read_bytes(), args.stage, args.launcher_pin,
                       args.composition_pin, args.transport_pin, args.source_pin, args.native_pin, args.turn_pin,
                       commissioned_source=args.commissioned_source)
    compile(rendered, args.output, 'exec')
    with Path(args.output).open('xb') as stream:
        stream.write(rendered)
    print(sha256(rendered).hexdigest())


if __name__ == '__main__':
    main()
