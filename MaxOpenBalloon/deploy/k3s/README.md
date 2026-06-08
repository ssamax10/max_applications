# K3S Deployment

1. Install K3S on target nodes.
2. Install Helm and ArgoCD into cluster.
3. Apply ArgoCD app manifests for each environment:
   ```bash
   kubectl apply -f ../argocd/application-dev.yaml
   kubectl apply -f ../argocd/application-stage.yaml
   kubectl apply -f ../argocd/application.yaml
   ```
4. Ensure external dependencies (PostgreSQL, MinIO, Redis, edge Authentik, OpenFGA) are available or modeled as additional charts.
