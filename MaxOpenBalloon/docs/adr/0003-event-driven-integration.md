# ADR 0003: Event-driven internal communication

## Status
Accepted

## Decision
Internal integration uses versioned domain events with tenant propagation (`tenant_id`) and trace propagation.

## Consequences
- Decoupled context integration.
- Requires schema governance and compatibility checks.
