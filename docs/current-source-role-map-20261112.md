# Current UIF source-role map

12 November 2026. Selected source: `89e0f761c4efb8ae32b66f37e6c33e068355d0d0`,
tree `cacfe2ea5b8935426ee829dd7ab1a999c773d7c4`. Documentation base: `612bcd9211a782995dac51147333b811aa32ae41`.
The UIF owner decision (SHA-256 `eb140aeb5178d4ed220b2cdb00153d858b0756f1ddf38fc543d8b7d7d3955abd`)
selects the contracts below. [Machine-readable pins and symbols](current-source-role-map-20261112.json)
identify every file. The coordinator reports PR42 at this source and hosted
qualification passed; this map does not infer merge, installation or BAU.

## Six current roles

| Role | Exact selected paths/symbols | Existing owner and actual scope |
| --- | --- | --- |
| Producer | `src/uif/owner_runtime.py::OwnerAuthority.claim`; `src/uif/publication_rpc.py::PublicationRPC.publish_candidate` / `prepare_pull_request` | Existing helper author / publisher: issue-backed claim and candidate/PR production with readback at this pin; integrator owns final composition. |
| Schema / validator | `config/delivery/stokowski-control.json`; `src/uif/role.py::verify_control_role`; `src/uif/validation.py::StageBinding`; `src/uif/publication.py::PublicationRequest`; `config/windmill/source/foo_jobs.schema.json` | Existing helper and guard/route/publisher consumer owners: manifest pins, stage/request bindings and transport schema; integrator owns the final manifest. |
| Sealing / receipts | `src/uif/publication.py::wire` / `fingerprint`; `src/uif/native_publisher.py::durable`; `src/uif/owner_runtime.py::OwnerAuthority.lock` | Publisher / helper author: canonical JSON, SHA-256, durable receipt writes and locks. A fingerprint is not a signature or old-sealer equivalence. |
| Settings | `config/delivery/role-launch.example.json`, `config/delivery/role-supervisor.example.json`, `config/delivery/shared-job-admission.example.json` | UIF integration/release owner: source templates. Null identities/grants/pins remain unrendered; epoch1 prepared owner-runtime/catalogue inputs need their own exact supplied pins. |
| Assets | `pyproject.toml`; control manifest above; `tools/delivery/uif_coding_child_guard.c`; `deploy/stokowski/uif-coding-dropper.sh` / `deploy/stokowski/uif-role-helper.sh` | UIF integrator / guard owner: control package and launch/guard templates, preserving Stokowski/Pool lineage; no mandatory product payload. |
| Operator | `src/uif/owner_runtime.py::bind_owner_coordinator`; `src/uif/supervisor.py::main`; `tools/delivery/uif_shared_job_admission.py` → `uif.shared_job_admission.main`; `tools/windmill/foo_user_exec.py::main` | Existing helper / guard / route owners retain their consumers; integrator owns final bootstrap/composition. Registration hook, per-issue supervisor, Pool admission and fixed ordinary-user transport. |

The control manifest is SHA-256
`9b043a2d056c9059a76d9bb44d5a61d7202b1e790f6c41519e3aa487b2640eb9`.
Its actual read-only `verify_control_role` check passes all 38 pinned files.
Selected Python symbols were verified by AST; no runtime tests or effects were
performed for this map.

## Actual adaptation still required

`RowKey` / `LedgerAdmission` already bind row, source, snapshot, owner, epoch and
original operation. However, `owner_runtime.py:528` excludes issue-less rows;
its intake wraps Linear issues. `supervisor.py:150–174` requires a UIF issue and
UUID. `PublicationRequest` at `publication.py:98–103` and `verify_origin` at
`native_publisher.py:331–339` preserve issue UUID requirements. These are
concrete issue-backed contributions at the pinned 89e source, not a fully
integrated independent ledger-task producer/consumer chain.

The coordinator subsequently reports the three-file identity component accepted
at `7dfdb09`, review reference `66742ea6`. This later component is separate from
the pinned 89e map. The existing helper author owns integration into the common
`ledger.py`, `admission.py`, `owner_runtime.py`, `stokowski.py` and their tests.
The integrator owns final manifest, bootstrap and composition. Existing guard,
route and publisher owners retain their disjoint consumers; integrated consumer
source still needs exact review. Preserve original task,
operation and proof bindings. Do not invent a Linear UUID, queue, service or
parallel product implementation. Identify the registered long-running
coordinator's executable bootstrap and actual typed caller: a library hook or
per-issue CLI is not that proof. This is a **source gap**, separate from later
installation, rendered settings, live readback and BAU qualification.

## Original-row disposition and history

For `assets-validator-composition-disposition` (UIF-216) and
`canonical-v1-selected-composition` (UIF-161), this document supplies the concrete
current role selection and source-map credit. It does not declare full-row
completion; typed-consumer integration and final exact source review remain
explicit. Installation is not a prerequisite for accepting this source map.

The owner selected these UIF contracts instead of making historical 996b assets,
b850 validators or the old canonical-V1 pair mandatory runtime inputs. Their
identities remain history: no recovered bytes, equality, old-effect completion,
deletion or automatic supersession of an actual consumer is asserted. Current
schema version names do not prove V1 equivalence. Preserve a historical contract
only where a future consumer actually selects it, then qualify that boundary.
