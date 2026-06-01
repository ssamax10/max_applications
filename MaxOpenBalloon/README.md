# MaxOpenBalloon

Enterprise-grade multi-tenant CAD collaboration platform monorepo for aerospace and manufacturing workloads.

## Core stack
- Frontend: React + TypeScript
- Backend: FastAPI services
- Data: PostgreSQL, Redis, MinIO
- Identity: Authentik (OIDC)
- Authorization: OpenFGA
- Observability: OpenTelemetry + Prometheus + structured JSON logging
- Platform: Docker Compose (local), K3S + Helm + ArgoCD (cluster)

## Bounded contexts / modules
- Drawing Service
- Geometry Service
- Balloon Service
- AI Service
- MCP Service
- Revision Service
- DWG Translation Service (network-isolated, GPL boundary)

## Viewer capabilities
- DWG via isolated translation service
- DXF support
- PDF support
- SVG rendering
- Konva annotation layer
- Balloon overlay layer

## Repository layout
- `apps/frontend`: React TypeScript web client
- `services/*`: FastAPI bounded-context services
- `contracts/openapi`: OpenAPI contracts per service
- `contracts/events`: event contracts (JSON Schema)
- `contracts/mcp`: MCP tool definitions
- `db/schemas`: PostgreSQL schemas
- `deploy/docker`: local docker compose
- `deploy/helm`: Helm chart
- `deploy/argocd`: ArgoCD application manifests
- `deploy/k3s`: K3S deployment guidance
- `docs/adr`: architecture decision records
- `compliance`: SBOM and dependency compliance artifacts

## Quick start (local)
```bash
cd MaxOpenBalloon/deploy/docker
docker compose up --build
```

## CI
GitHub Actions workflow validates frontend build and Python service lint/syntax gates.
