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


def compose(source, stage, launcher_pin, composition_pin, transport_pin, source_pin, native_pin, turn_pin):
    if sha256(source).hexdigest() != PREIMAGE:
        raise ValueError('Unrecognized parked launcher bytes')
    if not re.fullmatch(r'/opt/uif-stokowski/[a-z0-9][a-z0-9.-]{1,90}', stage) or stage == OLD_STAGE:
        raise ValueError('Expected an isolated absolute rollout stage')
    if not all(re.fullmatch('[a-f0-9]{64}', pin) for pin in (launcher_pin, composition_pin, transport_pin, source_pin, native_pin, turn_pin)):
        raise ValueError('Expected reviewed package member digests')
    src = source.decode().replace(OLD_STAGE, stage)
    for needle in (OLD_LAUNCHER, ORIGINAL_MAIN, ORIGINAL_CONFIG):
        if src.count(needle) != 1:
            raise ValueError('Unrecognized guarded launcher composition')
    src = src.replace(OLD_LAUNCHER, launcher_pin)
    src = src.replace(ORIGINAL_CONFIG, '''    _NativeSinks(orchestrator, _RemoteComposition)
    configure(runner, orchestrator, publication, **_RemoteComposition.options(),
              policy=Path(''')
    # Isolated mode (-I -S -B) remains mandatory even before importing the adapter.
    # The installed package tree and interpreter are independently pinned by the
    # service installer; verify new trusted adapter members prior to import.
    return src.replace(ORIGINAL_MAIN, f'''if __name__ == "__main__":
    if any(arg.partition("=")[0] == _GuardedAdminFlag for arg in _GuardedArgv[1:]) or not _GuardedMarker.is_file():
        guarded_staged_entry()
    if not (_GuardedFlags.isolated and _GuardedFlags.no_site and _GuardedFlags.dont_write_bytecode):
        raise SystemExit("Native authority requires the isolated installed interpreter")
    import hashlib as _StartupHash, sys as _StartupSys
    _Site = _GuardedPath({stage!r}) / "venv/lib/python3.12/site-packages"
    for _Name, _Pin in (("native_composition", {composition_pin!r}), ("remote_authority", {transport_pin!r}),
                         ("remote_source", {source_pin!r}), ("native", {native_pin!r}),
                         ("turn", {turn_pin!r}), ("launcher", {launcher_pin!r})):
        _Member = _Site / "uif/stokowski_cloudflare" / (_Name + ".py")
        if _Member.resolve() != _Member or _StartupHash.sha256(_Member.read_bytes()).hexdigest() != _Pin:
            raise SystemExit("Installed native authority adapter differs from the reviewed pin")
    _StartupSys.path.insert(0, str(_Site))
    from uif.stokowski_cloudflare.native_composition import load_composition as _Load, install_sinks as _NativeSinks
    _RemoteComposition = _Load(_GuardedPath({stage!r}) / "selector-v1/remote-authority.json")
    guarded_staged_entry(**_RemoteComposition.startup_arguments())''').encode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ('fixture', 'stage', 'launcher_pin', 'composition_pin', 'transport_pin', 'source_pin', 'native_pin', 'turn_pin', 'output'):
        parser.add_argument(arg)
    args = parser.parse_args()
    rendered = compose(Path(args.fixture).read_bytes(), args.stage, args.launcher_pin,
                       args.composition_pin, args.transport_pin, args.source_pin, args.native_pin, args.turn_pin)
    compile(rendered, args.output, 'exec')
    with Path(args.output).open('xb') as stream:
        stream.write(rendered)
    print(sha256(rendered).hexdigest())


if __name__ == '__main__':
    main()
