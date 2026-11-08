# DeployForge Kubernetes deployment

The Helm chart in `deploy/helm/deployforge` deploys the API, background worker, and dashboard. PostgreSQL is an external dependency; the chart does not install or manage a database.

## Before installing

1. Build and publish the API and dashboard images to a registry your cluster can pull from. Set their repository and immutable tag or digest in a protected values file.
2. Provision PostgreSQL and migrate its schema before starting the application. Supply its connection URL through a Kubernetes Secret rather than chart values.
3. Create the runtime Secret (default name `deployforge-runtime`) with these keys:

   - `database_url`
   - `session_secret`
   - `oidc_client_secret`
   - `build_callback_secret`
   - `github_actions_token` when GitHub Actions dispatch is enabled
   - `argocd_auth_token` when Argo CD reconciliation is enabled

4. Configure the OIDC redirect URI to `https://<host>/api/v1/auth/callback`. Keep MFA enforcement in the identity provider.
5. Configure DNS, an ingress controller, and a TLS Secret. Set `ingress.enabled`, `ingress.host`, and `ingress.tls.secretName` in protected values.

## Install

```sh
helm upgrade --install deployforge deploy/helm/deployforge \
  --namespace deployforge --create-namespace \
  --values /path/to/protected-values.yaml \
  --wait
```

Only the dashboard Service is routed by the optional Ingress. Nginx proxies `/api/v1/` to the internal API Service. The chart creates no Kubernetes Secret containing credentials; use a cluster secret manager and bind only the required registry pull credentials to the release.

The initial chart uses one replica per component. Configure and validate capacity, database connection limits, backups, and recovery with the operations owners before production traffic.
