# Architecture and actual interfaces

Current source clarification: 12 November 2026, against UIF
8fa48aa56ab1383f7b4df267fbd1da47f7996071. Read the current
[entrypoint](../AGENTS.md), [local policy](agents/uif-worker-policy.md) and
[policy manifest](agents/uif-worker-policy.json). They govern current work;
the historical interface record below is not a live binding.

## Execution domains and roles

Five fixed project roles cover UIF, OffMenu, CIX, ANQR and Circular Quay. Each has
its own authorized Cloud Git checkout, CPU and storage, independent of Foo.
The sixth Cloud infrastructure role owns the explicitly assigned
maldous/aldous-info-platform source and infrastructure/provider adapters, outside
the five-product delivery matrix. Existing live-site/publisher custody remains
separate. Fixed roles are logical assignments, not
immutable processes, dedicated physical hardware or guaranteed capacity.

Authorized project owners use their available GitHub interfaces and Foo MCP
directly for deterministic work. MCP is optional execution capacity, not a
reasoning host or a mandatory checkout. Cloud work can continue when Foo is
unavailable. Reasoning allowance, tool access and host resources are separate
observations.

Morgan coordinates Foo-local Codex session creation and resumption through the
native tools actually available to Morgan. Project owners retain scope,
decisions, detailed status and delivery ownership. Native analysis workers,
Cloud durable roles, deterministic MCP jobs and Foo Codex sessions must not be
counted or described as interchangeable admissions.

Cloud-primary and combined operation follow the
[shared policy](agents/uif-worker-policy.md). Actual source, issue, owner,
operation and evidence identity remain continuous through a handoff; their
receipts retain their true execution origin.

## Authority map

| Surface | Authority | Boundary |
| --- | --- | --- |
| UIF requirements and owner | Common infrastructure obligations, integration, dependencies and reusable lessons | Shared parent governance; no invented API hierarchy or product-history migration |
| Product requirements and owner | Product scope, local acceptance and implementation | Self-contained operation with explicit optional parent integration |
| UIF repository | Common architecture, methodology and shared implementation contracts | No duplicate execution state, private payload export or competing runtime |
| Product repositories | Exact source, toolchain, checks and product implementation | Bind repository, commit and tree; a branch name alone is navigation |
| Stokowski | Admitted coding attempts, leases and workspace/resource scope | A temporary worker is not automatically an admitted attempt |
| LangGraph / LangChain | Selected finite decisions and checkpoints | Checkpoint persistence is not proof of an external effect |
| Windmill / backend | Actual operation, terminal receipt and child termination | Wrapper success is not accepted output |
| Existing integration / publisher | Reviewed source integration and authorized publication | Integration, release, served state and functional acceptance differ |
| Private evidence custodian | Original receipts, logs, data and access-controlled artifacts | Publish only permitted bounded references and hashes |

UIF must not require any product's checkout, data or build assets to function.
Product-local adapters can depend on that product when explicitly selected.
Shared third-party infrastructure dependencies remain legitimate parent
dependencies. Current source-role separation and deployment migration belong to
their existing owners; this document does not adopt a replacement package.

## Current Foo route and qualification limits

Use the [Windmill workspace guidance](agents/windmill-workspace.md). Its scoped
connection checkpoint identifies workspace `foo`, the generic Foo Workspace
interface, and script `u/admin/foo_root_jobs` with the runner arguments nested
under `args`. The fixed legacy wrapper is not retargeted by that connection.

A connection inventory does not establish a root grant, credential placement,
host execution, native roots, lock closure, capacity or runtime readiness.
Historical `offmenu` receipt namespaces remain unchanged. Active shared
infrastructure naming and bindings must be project-neutral under the current
requirement; the actual source/deployment migration stays with its integrator.
Do not infer a new account, grant or service transition from a name.

## Historical interface record: 6 November 2026

The retained foundation source inspected the OffMenu-lineage candidate
b8500ca73f23605e2aaeb7270ae4de36bbf16633, tree
7ad44638e20942f1cfdcf04c61da49fcf99be064, on branch
feature/uif161-consumer-source-purpose-cloud. Its then-reported suspended state
is historical, not current writer availability.

The then-published product source 3540b2d4d5f4e96a518a63c832ccd6c3db809686,
installed-control reference 5b8ca36 and separate root handover were distinct
roles. They are not this repository's current execution baseline.

The following symbols were inspected in that candidate. Paths are relative to
its source repository; this table does not assert that a current installed
consumer exposes them unchanged.

| Historical interface | Responsibility recorded at that inspection |
| --- | --- |
| tools/delivery/native_harness_linear.py: plan_native_linear / NativeLinearAdapter | Warranted-work planning, validated effects and destination reconciliation |
| NativeLinearAdapter._validate / _readback / _work_readback / observe / step | Operation, packet, owner, issue, project and team bindings; exact destination comparison and prior-effect reconciliation |
| _SourceOutcomeMetadata.read / apply | Issue metadata, exact outcome comments, authorized effect, persisted outcome and destination readback |
| accepted_project_source / _source_chain | Accepted source facts and exact repository/project Git chain |
| finalize_source_outcome | Bounded finalizer requiring completed-native evidence and native journal acceptance in that candidate |
| tools/delivery/stokowski_stage.py: bind_reviewed_source | Reviewed-source preparation, journal and checkpoint binding |
| tools/delivery/native_harness_runtime.py: journal_acceptance | Selection of reviewed-source or native journal acceptance |
| tools/delivery/stokowski_adapter.py: _check_unit_review | Source, policy, output and independent-review checks |
| tools/delivery/native_adoption_consumer_validator.py: validate_wire / _named_checks | V1 source, role, profile and intake consumer boundary |

The seven historical adoption consumer IDs were current-owner-code-config,
owner-policy-new-role-profile-associations, generic-current-review-template,
boundary-current-context-UIF-102, boundary-current-context-UIF-117,
current-state-header and publisher-role-capability. They were not seven lifecycle
stages.

Canonical JSON/SHA-256 fingerprints, pinned sources and policies, authenticated
actors, custody, independent review and readback retain their actual scope.
A digest is not a cryptographic signature or an authority grant. No new
cryptographic gate is introduced.

## Current consumer qualification

For a current unit, inspect only its actual producer, origin, handoff and
selected consumer where that dependency applies. Historical reviewed-source
support does not prove arbitrary subagent-output acceptance. Do not fabricate a
lease, native session, completed-native receipt or resolved model to satisfy a
consumer.

A real incompatibility belongs to the existing runtime/source owner with scoped
impact and independent review. Preserve original readers and pending operations;
do not build a second implementation or retry an uncertain effect. The historical
source-outcome limitation remains evidence, not an automatic gate for unrelated
foundation source delivery.
