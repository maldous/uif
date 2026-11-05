# UIF — Universal Infrastructure Foundation

UIF is the shared foundation for physical and virtual infrastructure, project integration and delivery methodology across OffMenu, CIX, ANQR and CQ. It is the logical parent and bridge between the normal Linear / Stokowski / Windmill / LangGraph–LangChain harness and temporarily authorized native subagent execution.

Two operating modes share the same UIF Linear identity, evidence and methodology:

- **Local mode:** normal Foo local session-based Codex execution when local Codex tokens are available; this is BAU.
- **MCP mode:** fallback execution when local Codex tokens are unavailable; currently Morgan and native agents work through Foo MCP.

MCP mode is active for foundation delivery. Deferred local-mode BAU does not block other UIF work. Neither mode creates a second runtime or new quota entitlement.

This private repository owns common integration definitions, operating procedures, architecture decisions and references to reusable requirements in UIF Linear. Product repositories retain their implementation and product-specific contracts. Linear retains actionable requirements, decisions, source/evidence identities, acceptance and continuation state. This repository is not a second queue or evidence store.

## Current delivery

The current deliverable is a usable repository, workspace, methodology and project integration map. Documentation records verified interfaces and explicit unknowns; it does not certify that every route has executed. The existing runtime remains in its original custody. No private OffMenu harness, runtime data, credentials or historical evidence is copied here.

BAU restoration is a deferred UIF deliverable to be realized when token usage is restored. It is not a prerequisite for this foundation delivery. Restored quota alone will not establish adoption or autonomous delivery.

## Entry points

- [Agent ownership and boundaries](AGENTS.md)
- [Architecture and actual interfaces](docs/architecture.md)
- [Project integration and legacy dispositions](docs/project-integration.md)
- [Operating procedure and durable Linear records](docs/operating-procedure.md)
- [Evidence references and unresolved boundaries](docs/evidence.md)
- [Repository scope decision](docs/adr/0001-foundation-repository.md)

UIF uses one active canonical V1 contract. Historical payloads and readers retain their identities; no adapter, conversion, relabeling or replay is introduced. USF and older UIF/TID standards remain historical sources. TID semantics are future work, not a current integration dependency.

Use develop for reviewed integration and main for accepted releases. Work in a bounded worker branch; publication, merge and runtime adoption are separate actions.
