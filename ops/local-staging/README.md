# DeployForge local staging lab

This directory describes the isolated local validation environment. It is not
a production or highly available staging installation.

## Lab boundaries

- Kubernetes context: `deployforge-staging` (a separate Minikube profile).
- The pre-existing `minikube` profile and its workloads are not managed here.
- Argo CD is pinned to `v3.5.6` and installed in its standard non-HA form for
  local evaluation.
- The `platform` AppProject only trusts the DeployForge repository and the
  `demo-staging` destination. It allows the Namespace object because the
  sample Kustomize overlay owns the namespace and its restricted Pod Security
  labels.
- `Namespace` is cluster-scoped, so allowing that kind is broader than the
  namespaced workload destination. This is acceptable only in this isolated
  disposable lab. A production installation should provision the namespace
  separately, remove `namespace.yaml` from the workload overlay, and remove
  `Namespace` from `clusterResourceWhitelist`.
- Credentials and generated secrets must stay outside Git.

## Prepare the target

From the repository root:

```sh
kubectl --context=deployforge-staging apply -f ops/local-staging/platform-project.yaml
kubectl --context=deployforge-staging apply -f examples/demo-service/deploy/k8s/base/namespace.yaml
kubectl --context=deployforge-staging -n demo-staging get resourcequota,limitrange
```

The checked-in Argo CD Application points to the generated
`deployforge/staging` branch. That branch is created by the verified release
workflow only after it has built, scanned, signed, and attested an immutable
image. The `main` branch intentionally contains a zero-digest placeholder and
must not be deployed as a real release.

Do not create or sync the staging Application until that generated branch
exists and contains a verified image digest. The GitHub callback and provider
credentials must also be configured before starting a DeployForge release.
