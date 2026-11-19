# Flux GitOps integration

DeployForge can register and reconcile a Flux `Kustomization` for a service and environment. The worker talks directly to one Kubernetes API endpoint configured for the Flux cluster. API pods receive the public endpoint and cluster identity for configuration checks, but the bearer token is worker-only.

## Target requirements

- Register a service whose catalog repository URL matches the referenced Flux `GitRepository` URL. The adapter currently accepts credential-free HTTPS Git URLs.
- Set the DeployForge environment's cluster name to `DEPLOYFORGE_FLUX_CLUSTER_NAME` and set its namespace to the Kustomization's `.spec.targetNamespace` (or the Kustomization namespace when targetNamespace is omitted).
- Register the Flux Kustomization's namespace and name in GitOps targets. Target names are limited to 63 characters because they are also used as workload labels.
- Use a GitRepository in the same namespace as the Kustomization. Cross-namespace `sourceRef` is rejected.
- Give each DeployForge target its own `GitRepository`; do not share that source with another Kustomization or manage the source object's `.spec.ref` from a parent Flux reconciliation. The worker pins `.spec.ref.commit` to the exact approved source commit on each deployment and rollback, then requests reconciliation. This prevents a moving branch from deploying a newer commit than the approved build.
- Set `Kustomization.spec.wait: true` or configure `spec.healthChecks`, so Flux reports readiness only after workload health checks pass.
- For verified OCI artifact deployments, put `deployforge.io/target: <Kustomization-name>` on each workload's Pod template labels (Deployment, StatefulSet, DaemonSet, Job, and similar). DeployForge uses that exact label to scope live image digest inspection to the target's Pods.

Example target settings:

```yaml
apiVersion: source.toolkit.fluxcd.io/v1
kind: GitRepository
metadata:
  name: checkout
  namespace: flux-system
spec:
  interval: 5m
  url: https://github.com/example/checkout
  ref:
    commit: 0123456789abcdef0123456789abcdef01234567
---
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: checkout-production
  namespace: flux-system
spec:
  interval: 10m
  path: ./deploy/production
  prune: true
  wait: true
  targetNamespace: checkout-production
  sourceRef:
    kind: GitRepository
    name: checkout
```

Label the Pod template for each workload managed by this Kustomization:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: checkout
  namespace: checkout-production
spec:
  template:
    metadata:
      labels:
        deployforge.io/target: checkout-production
```

The example commit is illustrative; DeployForge updates `GitRepository.spec.ref.commit` to the immutable source revision on each run. It verifies that no other Kustomization in the namespace references the same GitRepository, then requests source and Kustomization reconciliation. It polls Flux's `Ready`, `Reconciling`, `Stalled`, and revision fields. The status endpoint shows the latest worker-observed run; it does not give API pods Kubernetes credentials.

## Runtime configuration

Set the following values in both runtime roles:

```dotenv
DEPLOYFORGE_FLUX_API_URL=https://kubernetes-api.example.com
DEPLOYFORGE_FLUX_CLUSTER_NAME=production-us-east-1
DEPLOYFORGE_FLUX_API_TIMEOUT_SECONDS=15
```

Keep `DEPLOYFORGE_FLUX_AUTH_TOKEN` only in the worker secret. In production Compose, put the token in `secrets/flux_auth_token`. In Helm, enable `config.flux.enabled`, set `config.flux.apiUrl` and `config.flux.clusterName`, and add `flux_auth_token` under `secrets.keys.fluxAuthToken` in the existing runtime Secret. TLS certificate verification stays enabled; the configured URL must be an HTTPS origin without a path.

Create a dedicated Kubernetes identity for this adapter. Grant only `get`, `list`, and `patch` on `kustomizations.kustomize.toolkit.fluxcd.io`, `get` and `patch` on `gitrepositories.source.toolkit.fluxcd.io`, and `list` on Pods in the target workload namespaces. The worker patches the GitRepository commit and reconciliation annotations, lists Kustomizations to reject shared sources, and lists target-labeled Pods for verified OCI image digest evidence. Scope RoleBindings to the reconciliation and workload namespaces, and do not grant cluster-admin. If the cluster API is private, allow worker egress to that endpoint and configure the Helm NetworkPolicy accordingly.

## Operational boundary

One DeployForge installation supports one Flux API endpoint and cluster identity. Flux Rollouts stage promotion and abort controls are not exposed; those controls remain specific to Argo Rollouts. GitRepository objects must be dedicated to a DeployForge target and not reconciled by another Kustomization because DeployForge updates their pinned commit during deployment and rollback.
