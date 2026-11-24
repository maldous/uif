# CircularQuay repository identity

The `circularquay` project entry in `config/white-room.json` names
`maldous/CircularQuay`, preserving the remote repository's canonical spelling.
The authoritative repository is [CircularQuay](https://github.com/maldous/CircularQuay).
This replaces an unresolved repository locator, not a project readiness decision.

The earlier narrow lowercase host-path probe in `discovery.md` did not establish
that the project was absent. Repository identity can be established independently
of host checkout discovery. This correction does not resolve a local path, move
source, retire a compatibility alias, or claim a fresh host inventory.

Both `foo` and `cloud` dependency-discovery flags remain false. No source commit,
profile, executor, owner, lease, capability, access right, output contract or
admission is selected by this mapping. Other projects and requirements remain
unchanged. The original selected analyzer-assessment source contract is separate
and is not repinned by a repository locator.

The offline evaluator can now compare CQ evidence and report incomplete or
mismatched requirements, rather than rejecting the unresolved repository name
before comparison. Its output still sets `execution_authorized` and
`runtime_readiness_verified` to false. Caller-supplied synthetic observations do
not authenticate a live environment or qualify the selected CQ execution path.

The configuration digest changes, so evidence bound to the previous configuration
must be reconstructed against the new contract rather than accepted unchanged.

Run the bounded regression suite with:

```sh
python3 -B -m unittest discover -s tests/white_room -p 'test_circularquay_binding.py' -v
```

Run all affected offline comparison tests with:

```sh
python3 -B -m unittest discover -s tests/white_room -v
```
