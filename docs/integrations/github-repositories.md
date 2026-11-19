# GitHub repository bootstrap

DeployForge can create a private GitHub repository from an approved Golden Path template. The API queues a durable request; a worker calls GitHub, verifies the resulting repository and source template, and updates the service catalog. Network calls and credentials stay out of the API request path.

## Configure the integration

Set the owner where new repositories should be created, and map each enabled Golden Path slug to a trusted template repository:

```dotenv
DEPLOYFORGE_GITHUB_REPOSITORY_OWNER=acme-platform
DEPLOYFORGE_GITHUB_REPOSITORY_TEMPLATES={"api-service":"acme-platform/api-template","web-application":"acme-platform/web-template","background-worker":"acme-platform/worker-template"}
DEPLOYFORGE_GITHUB_REPOSITORY_PRIVATE=true
```

For GitHub Enterprise Server, set `DEPLOYFORGE_GITHUB_SERVER_URL` to the instance origin and `DEPLOYFORGE_GITHUB_API_URL` to its REST API base, commonly `https://github.example.com/api/v3`.

Create a dedicated fine-grained token and mount it only into the worker as `DEPLOYFORGE_GITHUB_REPOSITORY_TOKEN_FILE`. Limit it to approved templates and the destination owner. GitHub documents template generation as requiring **Contents: read** plus **Administration: write**, or **Contents: read** plus **Repository creation: write**, depending on the token/resource owner; follow the permissions listed for your GitHub version. Add **Actions: write** and **Variables: write** so the worker can install the callback settings. The token must be allowed to act on the generated repositories as well as read private templates and create repositories. Use an organization-approved GitHub App installation or equivalent owner-scoped credential when a selected-repository token cannot access newly generated repositories. See [GitHub's template generation endpoint](https://docs.github.com/en/rest/repos/repos), [Actions secrets](https://docs.github.com/en/rest/actions/secrets), [Actions variables](https://docs.github.com/en/rest/actions/variables), and [fine-grained token permissions](https://docs.github.com/en/rest/authentication/permissions-required-for-fine-grained-personal-access-tokens).

The worker needs the same `DEPLOYFORGE_BUILD_CALLBACK_SECRET` and `DEPLOYFORGE_BUILD_CALLBACK_PUBLIC_URL` used by the API to bootstrap new repositories. Compose mounts the callback secret into the worker; Helm already uses the same runtime Secret key for both roles. Keep these values synchronized. The worker writes the callback URL hostname as the `DEPLOYFORGE_CALLBACK_ALLOWED_HOST` Actions variable and encrypts the shared secret with GitHub's repository public key before saving `DEPLOYFORGE_CALLBACK_SECRET` as an Actions secret. The repository workflow receives the full callback URL in its dispatch input. The callback secret is not stored in DeployForge's provisioning record.

The API receives only the owner, template mapping, and visibility configuration. In production, it rejects the repository token. The production worker requires the token and repository configuration together. Compose and Helm examples keep the token in the worker secret only.

## Request and operate a bootstrap

Register a service from a configured Golden Path. Its detail drawer offers **Create private repository**; the request uses the service slug as the repository name. The UI shows queued, in-progress, succeeded, or blocked state and polls active jobs. On success, the service record receives its repository URL and default branch. A platform engineer or workspace administrator can retry a blocked job after correcting the owner, template, permissions, or organization policy.

Jobs use bounded retries with exponential backoff for transient GitHub failures. Before creating a repository, the worker checks whether the destination already exists. This makes recovery safe when GitHub created the repository but the worker lost the response. Existing repositories are accepted only when GitHub reports the expected source template and configured visibility. After verification, the worker idempotently configures the callback secret and allowed host before marking the job complete. An unexpected repository is blocked for operator review.

Creation and retry requests, worker transitions, and failures are written to Audit Logs. Repository provisioning history is retained with its service and workspace. Template contents and external GitHub settings remain managed in GitHub.
