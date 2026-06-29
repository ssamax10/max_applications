# Authentication Setup Guide

This guide explains how to configure authentication for MaxOpenBalloon in different environments.

## Overview

MaxOpenBalloon uses OpenID Connect (OIDC) with Authentik for authentication. The setup differs between local development and production deployments.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Authentication Flow                        │
└─────────────────────────────────────────────────────────────┘

Development (Docker Compose):
  Browser → localhost:5173 → Vite Proxy (/authentik/) → apps.maxautocables.com (Authentik) → Backend Services
  Note: Token exchange is proxied through Vite dev server to avoid CORS issues.

Production (Helm/Kubernetes):
  Browser → maxopenballoon.example.com → auth.edge.example.com (Authentik) → Backend Services
```

## Local Development Setup

### Prerequisites

- Docker and Docker Compose installed
- Node.js 18+ and npm installed
- Git installed

### Step 1: Start Services

```bash
cd MaxOpenBalloon

# Use development environment file
cp deploy/docker/.env.development deploy/docker/.env

# Start all services
docker compose -f deploy/docker/docker-compose.yml up -d

# Wait for services to be ready (30 seconds)
sleep 30
```

### Step 2: Authentik Configuration (Development Instance)

The development environment uses the shared Authentik instance.

**Authentik Provider Details**:
- **Issuer**: `https://apps.maxautocables.com/application/o/max-open-ballon-dev/`
- **Authorization URL**: `https://apps.maxautocables.com/application/o/authorize/`
- **Token URL**: `https://apps.maxautocables.com/application/o/token/`
- **JWKS URL**: `https://apps.maxautocables.com/application/o/max-open-ballon-dev/jwks/`
- **Redirect URI**: `http://localhost:5173`

**Configuration Steps**:

1. **Access Authentik UI**: https://apps.maxautocables.com
   - Use your organization credentials

2. **Verify OAuth2/OpenID Provider** exists with the settings above

3. **CORS for Token Exchange** (handled automatically):
   - The Vite dev server proxies token exchange requests through `/authentik/` to avoid CORS issues
   - No Authentik CORS configuration is needed for local development
   - The `VITE_OIDC_TOKEN_ENDPOINT` is set to `http://localhost:5173/authentik/...` which the Vite proxy forwards to Authentik
   - If you prefer direct token exchange (without proxy), configure CORS in Authentik:
     - In the OAuth2/OpenID Provider settings, enable **CORS**
     - Add allowed origin: `http://localhost:5173`

4. **Get Application Credentials**:
   - Contact your Authentik administrator for the **Client ID** and **Client Secret**
   - Update `deploy/docker/.env` with these values:
     ```bash
     OIDC_CLIENT_ID=<your-client-id>
     OIDC_CLIENT_SECRET=<your-client-secret>
     ```

5. **Ensure Redirect URI is configured** in Authentik:
   ```
   http://localhost:5173
   ```

### Step 3: Start Frontend

```bash
cd MaxOpenBalloon/apps/frontend

# Install dependencies (first time only)
npm install

# Start development server
npm run dev
```

### Step 4: Test Authentication

1. Open http://localhost:5173
2. Click **Sign In with Authentik**
3. You'll be redirected to https://apps.maxautocables.com
4. Login with your organization credentials
5. You'll be redirected back to http://localhost:5173

### Local Development Files

**Frontend** (`apps/frontend/.env`):
```bash
VITE_OIDC_ISSUER=https://apps.maxautocables.com/application/o/max-open-ballon-dev/
VITE_OIDC_CLIENT_ID=maxopenballoon-frontend
VITE_OIDC_REDIRECT_URI=http://localhost:5173
VITE_OIDC_AUTHORIZATION_ENDPOINT=https://apps.maxautocables.com/application/o/authorize/
# Token endpoint uses Vite dev proxy to avoid CORS (dev only)
VITE_OIDC_TOKEN_ENDPOINT=http://localhost:5173/authentik/application/o/token/
```

**Vite Proxy** (`apps/frontend/vite.config.ts`):
- The Vite dev server proxies `/authentik/` requests to `https://apps.maxautocables.com`
- This avoids CORS issues during token exchange in local development
- The proxy target can be overridden via the `OIDC_PROXY_TARGET` environment variable
- **This proxy only runs during development** (`npm run dev`); it has no effect on production builds

**Backend** (`deploy/docker/.env.development`):
```bash
OIDC_ISSUER=https://apps.maxautocables.com/application/o/max-open-ballon-dev/
OIDC_JWKS_URL=https://apps.maxautocables.com/application/o/max-open-ballon-dev/jwks/
AUTH_REQUIRED=true
ALLOW_TENANT_HEADER_FALLBACK=true
```

## Production Deployment (Helm)

### Prerequisites

- Kubernetes cluster (v1.24+)
- Helm 3.x installed
- Production Authentik instance at `https://auth.edge.example.com`
- TLS certificates configured

### Step 1: Prepare Values

Create `deploy/helm/maxopenballoon/values-production.yaml`:

```yaml
# Use the provided values-production.yaml as template
# Update these values:
env:
  OIDC_ISSUER: "https://auth.edge.example.com/application/o/maxopenballoon/"
  AUTHENTIK_SECRET_KEY: "your-production-secret"
  POSTGRES_PASSWORD: "your-db-password"
  MINIO_ROOT_PASSWORD: "your-minio-password"
  
  # Frontend production URLs
  VITE_OIDC_ISSUER: "https://auth.edge.example.com/application/o/maxopenballoon/"
  VITE_OIDC_REDIRECT_URI: "https://maxopenballoon.example.com"
```

### Step 2: Configure Authentik (Production)

1. **Create OAuth2/OpenID Provider** in production Authentik:
   ```
   Name: maxopenballoon
   Redirect URI: https://maxopenballoon.example.com
   ```

2. **Create Application**:
   ```
   Name: MaxOpenBalloon
   Provider: maxopenballoon
   ```

3. **Get Production Credentials**:
   - Update Helm values with production Client ID and Secret

### Step 3: Deploy with Helm

```bash
cd MaxOpenBalloon

# Add Helm repository (if needed)
helm repo add maxopenballoon ./deploy/helm/maxopenballoon

# Install/Upgrade
helm upgrade --install maxopenballoon ./deploy/helm/maxopenballoon \
  -f deploy/helm/maxopenballoon/values-production.yaml \
  --namespace maxopenballoon \
  --create-namespace

# Verify deployment
kubectl get pods -n maxopenballoon
kubectl get ingress -n maxopenballoon
```

### Step 4: Configure DNS and TLS

1. **DNS**: Point `maxopenballoon.example.com` to your ingress controller
2. **TLS**: Ensure TLS certificates are configured in the ingress

### Production Files

**Helm Values** (`deploy/helm/maxopenballoon/values-production.yaml`):
- Production OIDC settings
- Multiple replicas (3)
- Autoscaling enabled
- TLS configured
- Monitoring enabled

**Environment** (`.env.example` - default):
```bash
OIDC_ISSUER=https://auth.edge.example.com/application/o/maxopenballoon/
AUTH_REQUIRED=true
ALLOW_TENANT_HEADER_FALLBACK=false
```

## Environment Comparison

| Aspect | Development | Production |
|--------|-------------|------------|
| **Authentik URL** | https://apps.maxautocables.com/application/o/max-open-ballon-dev/ | https://auth.edge.example.com/application/o/maxopenballoon/ |
| **Frontend URL** | http://localhost:5173 | https://maxopenballoon.example.com |
| **Auth Required** | true | true |
| **Tenant Fallback** | true | false |
| **OIDC Discovery** | Remote (shared dev) | Remote (production) |
| **Token Exchange** | Vite proxy (`/authentik/`) | Direct (same-origin) |
| **TLS** | Yes (shared) | Yes (production) |
| **Replicas** | 1 | 3+ |

## Troubleshooting

### Local Development Issues

**Problem**: `ERR_NAME_NOT_RESOLVED` for `auth.edge.example.com`

**Solution**: Ensure `apps/frontend/.env` has development Authentik URL:
```bash
VITE_OIDC_ISSUER=https://apps.maxautocables.com/application/o/max-open-ballon-dev/
```

**Problem**: Authentik not accessible

**Solution**: Verify the Authentik URL is correct:
```bash
# Test OIDC discovery
curl https://apps.maxautocables.com/application/o/max-open-ballon-dev/.well-known/openid-configuration

# Check network connectivity
ping apps.maxautocables.com
```

**Problem**: CORS errors during token exchange

**Error**: `Access to fetch at 'https://apps.maxautocables.com/application/o/.../token' from origin 'http://localhost:5173' has been blocked by CORS policy`

**Solution** (preferred): The Vite dev server proxy should handle this automatically. Ensure:
1. `VITE_OIDC_TOKEN_ENDPOINT` is set to `http://localhost:5173/authentik/application/o/token/` (the generic Authentik token endpoint, not the app-specific one which only accepts GET)
2. The Vite dev server is running (proxy only works with `npm run dev`, not with static builds)
3. `OIDC_PROXY_TARGET` is set correctly (defaults to `https://apps.maxautocables.com`)

**Solution** (alternative): Configure CORS directly in Authentik:
1. Go to Authentik Provider settings
2. Enable **CORS** for the OAuth2/OpenID Provider
3. Add allowed origin: `http://localhost:5173`
4. Save the provider configuration
5. Set `VITE_OIDC_TOKEN_ENDPOINT` back to the direct Authentik URL

**Problem**: Login redirects fail

**Solution**: Ensure redirect URI matches in Authentik:
- Authentik: `http://localhost:5173`
- Frontend `.env`: `VITE_OIDC_REDIRECT_URI=http://localhost:5173`

### Production Issues

**Problem**: Authentication fails in production

**Solution**: Verify:
1. `OIDC_ISSUER` matches production Authentik URL
2. `VITE_OIDC_REDIRECT_URI` matches production domain
3. TLS certificates are valid
4. Authentik provider has correct redirect URI

**Problem**: CORS errors

**Solution**: Update `CORS_ALLOW_ORIGINS` in production:
```bash
CORS_ALLOW_ORIGINS=https://maxopenballoon.example.com
```

## Switching Between Environments

### Development Mode

```bash
cd MaxOpenBalloon/deploy/docker
cp .env.development .env
docker compose up -d

# Frontend will use: https://apps.maxautocables.com/application/o/max-open-ballon-dev/
# Backend will use: https://apps.maxautocables.com/application/o/max-open-ballon-dev/
```

### Production Mode

```bash
# For Docker Compose (if needed)
cd MaxOpenBalloon/deploy/docker
cp .env.example .env
# Edit .env and set production values
docker compose up -d

# For Helm (recommended for production)
helm upgrade --install maxopenballoon ./deploy/helm/maxopenballoon \
  -f deploy/helm/maxopenballoon/values-production.yaml
```

## Security Notes

1. **Never commit `.env` files** - They contain secrets
2. **Use strong passwords** in production
3. **Enable TLS** in production
4. **Rotate secrets** regularly
5. **Use Kubernetes secrets** in production (not environment variables)

## Additional Resources

- [Authentik Documentation](https://docs.goauthentik.io/)
- [OpenID Connect Specification](https://openid.net/specs/openid-connect-core-1_0.html)
- [Helm Documentation](https://helm.sh/docs/)

## Support

For issues:
1. Check logs: `docker compose logs <service-name>`
2. Verify environment variables
3. Test OIDC discovery: `curl https://apps.maxautocables.com/application/o/max-open-ballon-dev/.well-known/openid-configuration`
