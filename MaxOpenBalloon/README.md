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

## Development environment (Docker Desktop)

### Prerequisites
- Docker Desktop with Compose V2 enabled
- At least 8 GB RAM allocated to Docker Desktop

### Configure environment variables
```bash
cd MaxOpenBalloon/deploy/docker
cp .env.example .env
```

On Windows PowerShell, use:

```powershell
Copy-Item .env.example .env
```

### Start stack (baseline)
```bash
cd MaxOpenBalloon/deploy/docker
docker compose up --build -d
```

This default mode assumes your edge Authentik is used as the OIDC issuer.

### Start stack (developer hot reload)
```bash
cd MaxOpenBalloon/deploy/docker
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build -d
```

### Start stack with local Authentik (optional)
Use this only when you want a local identity provider instance instead of edge Authentik:

```bash
cd MaxOpenBalloon/deploy/docker
docker compose --profile local-auth -f docker-compose.yml -f docker-compose.dev.yml up --build -d
```

When using local Authentik profile, set in `deploy/docker/.env`:
- `OIDC_ISSUER=http://authentik:9000/application/o/maxopenballoon/`

### Stop stack
```bash
cd MaxOpenBalloon/deploy/docker
docker compose down
```

### Service endpoints
- Frontend: http://localhost:5173
- Drawing Service: http://localhost:18001/health
- Geometry Service: http://localhost:18002/health
- Balloon Service: http://localhost:18003/health
- AI Service: http://localhost:18004/health
- MCP Service: http://localhost:18005/health
- Revision Service: http://localhost:18006/health
- DWG Translation Service: http://localhost:18007/health
- OpenFGA: http://localhost:8081
- Authentik: http://localhost:19000
- MinIO API: http://localhost:9010
- MinIO Console: http://localhost:9011
- PostgreSQL: localhost:5432

### Backend production baseline included
- JWT/OIDC-aware tenant context enforcement in all FastAPI services (`AUTH_REQUIRED=true` to enforce bearer validation)
- Standardized health endpoints: `/health`, `/health/live`, `/health/ready`
- Prometheus metrics endpoint: `/metrics`
- Request count and latency metrics middleware
- Optional OTLP trace export via `OTEL_EXPORTER_OTLP_ENDPOINT`

### Enable strict auth locally (optional)
Set these values in `deploy/docker/.env`:
- `AUTH_REQUIRED=true`
- `OIDC_JWKS_URL=<issuer-jwks-url>`
- `OIDC_ISSUER=<issuer>`
- `OIDC_AUDIENCE=<audience>`
- `TENANT_CLAIM=tenant_id`

### Frontend Authentik sign-in (OIDC PKCE)
The frontend now supports Authorization Code + PKCE login against Authentik.

Set these values in `deploy/docker/.env` for browser login:
- `VITE_OIDC_ISSUER=<issuer>`
- `VITE_OIDC_CLIENT_ID=<public-spa-client-id>`
- `VITE_OIDC_REDIRECT_URI=http://localhost:5173`
- `VITE_OIDC_SCOPE=openid profile email`
- `VITE_TENANT_CLAIM=tenant_id`

If issuer discovery is not reachable or returns 404 in your environment, set these exact values copied from the Authentik provider screen:
- `VITE_OIDC_AUTHORIZATION_ENDPOINT=<Authorize URL>`
- `VITE_OIDC_TOKEN_ENDPOINT=<Token URL>`

In Authentik, ensure the frontend client is configured as a public client with:
- Redirect URI `http://localhost:5173`
- PKCE enabled (`S256`)

### Make shortcuts
```bash
make docker-up
make docker-dev
make docker-up-local-auth
make docker-dev-local-auth
make docker-logs
make docker-down
```

## CI
GitHub Actions workflow validates frontend build and Python service lint/syntax gates.

## Cluster environment mapping
- Helm values files:
	- `deploy/helm/maxopenballoon/values-dev.yaml`
	- `deploy/helm/maxopenballoon/values-stage.yaml`
	- `deploy/helm/maxopenballoon/values-prod.yaml`
- ArgoCD applications:
	- `deploy/argocd/application-dev.yaml`
	- `deploy/argocd/application-stage.yaml`
	- `deploy/argocd/application.yaml` (production)

All cluster environments are configured to use external edge Authentik through OIDC values, not the local compose Authentik profile.
