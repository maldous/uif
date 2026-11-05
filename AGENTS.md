# Working in UIF

## Authority and ownership

Read README.md and docs/operating-procedure.md first. Resolve the canonical Linear obligation, project, source baseline, current writer and affected consumers before edits. UIF-161 covers shared material contract alignment; UIF-108 is the existing planning/provenance lane. These references do not create execution admission or permission to change another owner's files.

Repository commands run as the verified normal user (currently Foo user, UID1000). Root transport is infrastructure access, not repository authorship. Never modify global Git identity. Reuse a verified canonical identity only in repository-local configuration.

One owner writes a selected branch/worktree/path scope at a time. Preserve other owners' WIP and original operation receipts. A suspended worktree is read-only until an explicit ownership transfer; do not reuse it merely because it is clean or idle. Record source commits and review/readback separately from runtime installation.

## Scope

This repository owns shared integration definitions and runbooks. Keep product source in its product repository and current private harness implementation in its existing repository/custody. Do not copy private source, credentials, runtime configuration, raw evidence or private histories into UIF. Link authorized evidence by immutable identity and bounded reference.

Maintain one active V1 contract at its actual authority. Do not create a competing schema, adapter, scheduler, ledger or shadow dispatcher. Record incompatible or missing inputs explicitly; never convert old prepared payloads to make a new contract appear satisfied.

## Execution and reporting

Check source identity, route/host/user, permissions, toolchain, capacity/quota and active leases independently. An available MCP route is not a product grant. Do not invent normal harness admission, model observations, completed-native receipts or reviewer independence for native subagent work.

Linear receives necessary issue-specific durable decisions, source/evidence references, acceptance and continuation facts. Ephemeral command status, polls and progress belong in MCP/native events/chat. Honor requested activity updates without flooding Linear.

Respect each project's holds and source pins. Current foundation work does not authorize service activation, production effects, CIX START/workloads/benchmarks, paid providers or new credentials. BAU is deferred; do not turn it into a gate for documentation and workspace readiness.

Before publication, obtain independent review of the exact candidate and verify links, source identities, scope and absence of private content. Push/merge/adoption need their own scoped authority; a local commit does not imply any of them.
