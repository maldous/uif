# Windmill declaration, mock-job, copy and transport protocols

Bounded UIF-249/UIF-260 source account, **maldous/uif only**, at commit
`43fa5e645e1cdb91ec75136a7509b8c88c085ab9`, tree
`3e527ec703921bb8efe9e8087b16e5fdcfdcfbae`. This documentation does not call Foo,
Windmill, SSH, a credential resolver, REST, a synthetic server or a source test.
No module was imported or executed. Existing [qualification guidance](../../windmill/qualification.md)
remains canonical operational context; no new runner, restoration route or
permission is introduced.

The three source files (417 lines) and three test files (422 lines, 25 named
methods) received complete reads. Four small bundle/schema/provenance files were
read as context. Large fixed inventory/disposition snapshots are **not** a new
per-item operational inventory in this pass; their fixed-digest checks are
source behavior. Counts are not measured coverage or newly passed tests.

| Bound input | Git blob | Lines / test methods |
| --- | --- | ---: |
| `tools/windmill/white_room.py` | `fac16a33a37b7b37de5f2a1a01ecb00ee38a76ca` | 216 / N/A |
| `tools/windmill/recreate.py` | `482308ef80ae325a9051186702f20cfa9be45529` | 121 / N/A |
| `config/windmill/source/foo_root_jobs.py` | `b0666c0671002283814a986ba8a60fc21d9ea396` | 80 / N/A |
| `tests/windmill/test_white_room.py` | `1a429196f68273993c1da101efbf8b083231374a` | 156 / 10 |
| `tests/windmill/test_recreate.py` | `03869e62eec93b403b4588715d4cfee20c7556d4` | 186 / 12 |
| `tests/windmill/test_exported_runner.py` | `e841a0e647ab9324d07aa914b2795ab8688dd544` | 80 / 3 |
| `config/windmill/deployment-bundle.json` | `b15bd3a6f88acb3408d3a70781d1ec1d52b16958` | Bundle context |
| `config/windmill/request.schema.json` | `52fe452c124f7592dc5d59de22d07a6c5c981392` | Offline schema context |
| `config/windmill/source/foo_root_jobs.schema.json` | `b3037482dfe06d2ba22437a57b9d5432a3368932` | Preserved transport schema context |
| `config/windmill/source-provenance.json` | `541e87b561b66d12a43bd53986d628284ac91759` | Dated custody context |

## `UIF-WM-DECLARATION`: fixed baseline validation and a non-executing plan

Owner: UIF configuration/recreation custodian. In
[white_room.py](../../../tools/windmill/white_room.py), `validate` loads the fixed
checked-in desired, observed and disposition JSON. Canonical object digests bind
both snapshots, preventing even a paired inventory/disposition omission from
silently becoming coverage. Source/schema byte pins and exact owner/transport/
workspace/credential/runner/dependency/blocker declarations must also match.

Each unique baseline `(type, path)` must have exactly one disposition. Existing
transport resources are retained, schedules remain disabled, the selected
archive remains retained-archived and other items remain hold-preserve; deletion
flags must be false. This is coverage of the **fixed dated baseline**, not live
consumer discovery, permission readback or a current server inventory.

A valid declaration still has no selected recreation workspace or credential
destination, no qualified deployed-runner/host-protocol pins, no project schedule
and `live_recreation_verified=False`. `plan` returns steps/blockers/rollback text
and an empty runtime-mutation list. It performs none of those steps and grants
no permission. The loader is ordinary JSON, not a complete general-purpose
strict schema validator; specific equality/digest checks enforce this selection.

The offline request protocol is exactly `submit` with identity probe or `status`
by 1–64-character alphanumeric/underscore/hyphen job ID. Extra request fields,
arbitrary probes and unsupported actions refuse **before** mock credential
resolution. The checked-in JSON schema documents that protocol; the Python
validator enforces it without a schema-engine install. It is not equivalent to
the preserved runner's `run/status/cancel` command/cwd/timeout interface.

## `UIF-WM-MOCK-JOB`: local uncertainty before a synthetic effect

Owner: a caller's local SQLite fixture, not a remote claim service.
Constructing `WhiteRoom` connects/creates the database and jobs table and commits;
it is not read-only filesystem discovery, even if the next request is status.

| Existing row / event | Transition | Failure/retry boundary |
| --- | --- | --- |
| Missing job, valid `status` | Return `unknown`; no job insertion by `handle` | No remote lookup or evidence of absence elsewhere |
| Missing job, valid `submit` | `BEGIN IMMEDIATE`; insert `uncertain` and commit before resolver/transport | Local transaction serializes same-ID admission, not remote effects |
| First admitted call | Resolve only the synthetic opaque handle, then exact mock identity response | Wrong identity or ordinary adapter exception leaves uncertain row; exception body is not returned |
| Exact mock identity succeeds | Commit row `succeeded` | Local commit is separate from the synthetic callback, not a distributed atomic transaction |
| Any existing row, repeated submit/status | Return retained state without another transport call | An uncertain original is not resubmitted or replaced automatically |

A BaseException/interruption after admission can also leave the already committed
uncertain intent. Independent clients can observe uncertainty while the first
call completes. Output always labels evidence synthetic-only and live recreation
false. The database path, private custody and SQLite/power-loss environment are
not qualified by this model; it introduces no cross-host lease/fence, source
publication or durable production authority. No timer/backoff/cancel/cleanup
protocol is implemented here.

## `UIF-WM-MOCK-RECREATE`: fresh fixture directory and same-job readback

After declaration validation, `recreate_mock` exclusively creates a nonexistent
selected directory, without creating its parents. It opens a synthetic database,
submits one fixed identity job, closes the client, opens a second client and
reads that same job ID by status, then writes an explicitly synthetic receipt.

This path **does create local files**. It does not execute SSH, create a Windmill
workspace, provision credentials or switch consumers. An existing directory
refuses; a partial directory/database can remain after failure and is not
automatically removed or retried. The helper adds no private-mode/ownership
policy for that selected directory. CLI modes `validate`, `plan` and
`recreate-mock` print JSON and return 0 on their own success; listed offline
input/file/SQLite errors return 2 with fixed failure metadata. Source-only or
mock success is never a live restoration receipt.

## `UIF-WM-REST-COPY`: approved bundle to a distinct script-copy receipt

Owner: the integrating caller with an authenticated transport, separately
approved administrative writes and credential-binding callback. The
[REST adapter](../../../tools/windmill/recreate.py) has no built-in live CLI,
authentication fallback, secret input, script execution, deletion or cutover.
The API/transport deadline and response bounds belong to the injected transport.

| Phase / event | Guard and externally visible step | Retained evidence / boundary |
| --- | --- | --- |
| Validate target | Lowercase isolated target syntax, length 3–64; active `foo` and preserved `offmenu`/`admins` forbidden | Syntax is not proof a workspace is authorized or isolated |
| Validate payload | Exact bundle field set/kind/fixed runner+credential identity; UTF-8 hash-pinned source/schema/lock; nonempty source and lock, object schema with properties | Complete before journal or transport; nonempty lock is not proof of genuine dependency resolution |
| Fence local attempt | Exclusively create original `pending` journal and file fsync | Existing journal refuses automatic replay; parent-directory durability/permissions are not separately established |
| Observe absence | POST workspace-exists returns **exactly False** | Otherwise hold, leaving journal |
| Create workspace | POST create requires 201 | May already have happened if reply/follow-up is lost |
| Bind external credential | Owner callback returns **exactly True**, then variable-exists GET returns **exactly True** | Adapter receives no credential bytes; existence is not complete persistent permission qualification |
| Create/read script | POST fixed script payload requires 201 and 16-lowercase-hex version hash; GET compares path/content/language/schema/lock/hash and requires no lock errors | No `parent_hash` update of an existing script or execution request |
| Verified copy | Create separate exclusive `.receipt.json`, file fsync; preserve original pending journal | State `script-copy-verified`; live recreation, permissions and host protocol flags remain false |

`read_pinned` resolves each artifact beneath the selected bundle root and checks
its byte digest. It does not prohibit every in-root symlink, impose read-size
caps or authenticate review provenance merely from `source_kind`. Hashes, source
rights and the actual approved export remain caller obligations. This copy
adapter reads the supplied bodies; it does not export a missing/denied body.

Within the REST/credential sequence, ordinary failures produce one sanitized
incomplete/uncertain error and retain the original journal. Do not delete a
partially created workspace or switch routes to replay. A receipt-file creation/
fsync failure occurs **after** that guarded sequence and can propagate despite
successful remote writes/readback. The original pending journal still needs
reconciliation; missing receipt does not prove no effect. Journal/receipt file
fsync is not a documented parent-directory fsync or cross-host claim fence.

The checked-in bundle deliberately lacks a resolved lock pin and therefore
refuses before journal creation or REST calls. Empty-lock substitution also
refuses. Package requirement comments are not a transitive lock. The pinned
upstream API definitions are documentary version1.864.1; compatibility with any
currently deployed server is not established by this pass. No new export,
credential destination, workspace or recreation was requested or attempted.

## `UIF-WM-CUSTODY-TRANSPORT`: preserved source is not a qualified replacement

The 80-line preserved runner is **dated repository custody source**, not a fresh
read of today's deployed helper or the denied host protocol. Its docstring says
deploy disabled, while actual `ENABLED=True` is retained as a separate fact.
It declares external package versions; the supplied source schema has optional
fields/defaults, while the Python function performs its action-specific checks.

The function accepts `run/status/cancel` and a fixed job-ID grammar. Run requires
nonempty bounded UTF-8 command bytes, absolute-looking cwd without NUL, and an
actual integer timeout 1–1,800 seconds; non-run requests cannot carry a truthy
command. These are transport-input guards, **not a command sandbox or path
containment/rights check**. Only after those guards does it resolve the configured
secret reference, parse a key, pin the public host key, and connect with agent/key
search disabled. No secret value is reproduced here or was read by this pass.

It sends JSON to one fixed SSH protocol command, drains stdout with a 256 KiB cap,
rejects stderr/nonzero exit and enforces a polling deadline. The reply must have
matching version/job ID and an allowed state. Same-ID status is a remote read
request, not proof of durable host idempotency; cancel is a request, not stopped-
child proof. The host-side implementation and actual identity/permissions must
be separately qualified, never inferred from this wrapper.

Known preserved privacy limits remain: secret resolution/key parsing occur
outside the connection try/finally and can propagate backend exception text;
an otherwise accepted receipt's extra fields are returned unchanged. This source
is **not qualified as a secret-safe replacement**. Synthetic tests exercise
selected guards, not complete arbitrary-output sanitization or live SSH. Do not
relax denials, copy credentials or execute this export to fill missing evidence.

## Source-test oracles and residuals

- **Offline/mock, 10 methods:** fixed declarations/236-item baseline coverage,
  paired omissions, request refusal, no overwrite, non-executing plan, same-ID
  reopen/concurrent clients, retained uncertainty and wrong-identity refusal.
- **REST copy, 12 methods:** loopback fake server and synthetic source/lock;
  checked-in missing/empty lock, official request shape, exact five-request copy,
  same-journal refusal, drift/existing workspace/credential/target/pin/path guards
  and exception-body sanitization. No real source execution or Foo API.
- **Preserved runner, 3 methods:** synthetic imported dependencies, invalid
  request before credential resolution, same-ID run/status and wrong receipt ID.
  Neither a real private key nor real SSH is involved in those test fixtures.

These are inspected test-source assertions, not new test results. UIF-249/260
receive five bounded protocol descriptions; complete inventory and independent
coverage integration remain separate. UIF-120/130/247/268 retain actual source,
permissions, jobs/consumers, installed/runtime and release/readiness obligations.
Private originals, disabled schedules, denied host exports and uncertain effects
remain in their owning custody. Existing PR77/78/79 are unchanged. Revalidate
affected assertions when these blobs, schemas, bundle or consuming contracts
change; do not replay accepted operations for documentation bookkeeping.
