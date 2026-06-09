# Dependency Compliance Report

## Policy
- LGPL dependencies: dynamic linking only.
- GPL dependencies: isolated into independent network services.
- Prohibit static linking to GPL libraries.

## Enforcement points
- DWG translation workloads are isolated in `services/dwg-translation-service`.
- Service interfaces enforce network boundaries.
- SPDX SBOM maintained at `compliance/SPDX-SBOM.spdx.json`.
