# ADR 0001: Separate UIF foundation repository

Status: current scoped foundation decision, 6 November 2026.

## Context

UIF is the current shared physical and virtual infrastructure foundation and bridge between normal harness delivery and authorized temporary native execution. Legacy UIF/USF/TID sources have historical purposes and private product runtime source already exists elsewhere. Reusing their names or copying their content wholesale would obscure actual ownership.

## Decision

Use private maldous/uif and normal-user workspace /home/user/src/uif for common integration definitions, architecture, operating procedures, applicability and links to durable UIF Linear requirements. Product records retain their own scope, source, rights and acceptance. Current Linear projects remain actual sibling records under the UIF team; logical parenthood does not manufacture unsupported hierarchy.

Reuse existing runtime V1 contracts, integrity/authority bindings, effect reconciliation and private custody. Do not create a new schema, adapter, scheduler, queue, memory ledger or cryptographic requirement. Do not copy private OffMenu harness or original evidence here. TID semantics are future work; historical records remain unchanged.

Use worker branches for bounded changes, develop for reviewed integration and main for accepted releases. A local commit, push, merge, deployment and runtime adoption are separate facts.

## Consequences

Foundation methodology and environment readiness can be delivered now. Live BAU remains deferred and is not its acceptance gate. Runtime gaps require exact source/consumer evidence, canonical Linear scope, existing-owner coordination and independent review. Reusable lessons flow through UIF Linear with explicit product/profile applicability, rather than product-history duplication.
