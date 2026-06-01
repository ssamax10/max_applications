# ADR 0001: Strict bounded contexts

## Status
Accepted

## Context
The platform spans drawing ingestion, geometry processing, ballooning, revisions, AI augmentation, and MCP integrations in a multi-tenant SaaS environment.

## Decision
Each module is implemented as an independent service with explicit contracts. Cross-context data access is prohibited; interactions occur through HTTP APIs and versioned domain events.

## Consequences
- Clear ownership and deployability boundaries.
- Higher governance overhead mitigated with shared contracts in `contracts/`.
