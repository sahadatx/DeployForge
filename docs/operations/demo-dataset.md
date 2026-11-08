# Local demo dataset

The optional Northstar Commerce Demo dataset provides connected sample records for a local product walkthrough. It runs only when the API is in a local development environment with development authentication enabled.

Create or sign in to a local account, then seed the workspace from the API container:

```sh
docker compose exec api python -m app.demo_data seed --email you@company.com
```

The command creates the `northstar-commerce-demo` workspace and adds the selected local account to it. The synthetic workspace contains 110 services, 120 releases, linked build and artifact history, GitOps and infrastructure activity, policy decisions and exceptions, audit events, telemetry sources, SLOs, alerts, and 50 workspace members. Records use synthetic names, timestamps, and provider references; no external provider jobs are dispatched.

Demo member accounts use reserved example identities and are inactive. They have no credentials and cannot sign in. Other workspaces and their memberships are not modified. The seed is safe to rerun: it fills missing records in the demo workspace without duplicating existing ones.

To switch back to your existing workspace, select it in the dashboard workspace menu.
