# Synthetic regression fixtures

`synthetic-legacy-status.json` is generated entirely by
`synthetic-legacy-status.mjs`. Its clock, source configuration, aliases, roster
generation and event bodies are invented test constants. The generator invokes
the real Worker against an in-memory SQLite D1 shim. It never reads a captured
status response, live configuration, identity, credential, event or network source.

The scenario has two unknown legacy manual sessions, an independently fresh
synthetic allowance and an unobserved curation source. It preserves regression
coverage for stale observations, neutral unmapped topology, all 70 configured
capacity positions, incomplete discovery, canonical revision hashing, historical
snapshot replay and separation from runtime/native-activity schemas.

`synthetic-render-sha256.json` pins eight outputs of the unchanged reviewed
renderer: full/compact, light/dark, and ordinary/minimal-page options. Regeneration
first asserts the approved 91/13 cell counts, neutral cells and absent percentage.
These expected hashes intentionally changed when captured operational test data
was replaced by an independently generated synthetic scenario. They are not a
claim that production output or design changed.

Regenerate from the service directory:

```sh
node test/generate-synthetic-fixtures.mjs
npm test
```

`synthetic-fixture.test.mjs` independently regenerates the fixture on every test
run and checks exact equality, its synthetic clock and semantic invariants.
The previous captured payload and its derived render hashes are excluded from
source publication. Production never imports these fixtures or generators.
