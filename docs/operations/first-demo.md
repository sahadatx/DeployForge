# First local demo

You do not need a GitHub token, cloud account, Kubernetes cluster, or service of your own to start. This walkthrough runs one small example service on your computer.

## Start the demo service

From the DeployForge project directory, run:

```sh
docker compose -f examples/demo-service/compose.yaml up --build -d
```

Open [http://localhost:8090](http://localhost:8090). The page shows the service version, release label, instance name, and health state. Its readiness endpoint is [http://localhost:8090/health/ready](http://localhost:8090/health/ready).

The existing dashboard remains at [http://localhost:5173](http://localhost:5173). The demo service is deliberately separate from real release records: it does not pretend to have passed a GitHub build, signature verification, approval, or Kubernetes deployment.

To see how this sample connects to the platform's CI, supply-chain, and GitOps contracts, continue with the [demo service release path](demo-release-path.md). That guide prepares the workflow and staging manifests; publishing and connecting GitHub/Argo CD remain explicit setup steps.

## Stop it

```sh
docker compose -f examples/demo-service/compose.yaml down
```

## What this proves

This confirms the local container can run a service and report health. DeployForge's live build and GitOps workflow needs a GitHub repository and a Kubernetes/Argo CD target; add those only when you are ready to connect a real staging environment.
