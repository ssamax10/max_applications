# ADR 0005: Licensing and isolation controls

## Status
Accepted

## Decision
- LGPL dependencies must be dynamically linked only.
- GPL components are isolated behind network service boundaries (DWG translation service).
- No static linking against GPL libraries.
- SPDX SBOM and dependency compliance reports are generated and versioned.

## Consequences
- Reduced copyleft propagation risk.
- Additional operational controls needed for isolated translation services.
