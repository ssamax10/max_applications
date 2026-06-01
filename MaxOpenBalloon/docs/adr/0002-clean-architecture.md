# ADR 0002: Clean architecture service internals

## Status
Accepted

## Decision
Service code follows `api`, `domain`, `core`, `events`, and `telemetry` layers.

## Consequences
- Domain logic remains framework-agnostic.
- Infrastructure concerns (logging, OTel, persistence adapters) stay replaceable.
