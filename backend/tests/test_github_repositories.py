import json

import httpx

from app.integrations.github_repositories import (
    GitHubRepositoryClient,
    GitHubRepositoryConfiguration,
)


def test_github_repository_client_generates_private_repository_from_template():
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            201,
            json={
                "id": 41,
                "full_name": "acme-platform/checkout-api",
                "private": True,
                "default_branch": "main",
                "template_repository": {"full_name": "acme-platform/api-template"},
            },
        )

    client = GitHubRepositoryClient(
        GitHubRepositoryConfiguration(token="repository-token", api_url="https://api.github.test"),
        transport=httpx.MockTransport(respond),
    )
    repository = client.generate_from_template(
        template_owner="acme-platform",
        template_name="api-template",
        owner="acme-platform",
        name="checkout-api",
        description="Checkout API",
        private=True,
    )

    assert repository["full_name"] == "acme-platform/checkout-api"
    request = requests[0]
    assert request.method == "POST"
    assert request.url.path == "/repos/acme-platform/api-template/generate"
    assert request.headers["authorization"] == "Bearer repository-token"
    assert json.loads(request.content) == {
        "owner": "acme-platform",
        "name": "checkout-api",
        "description": "Checkout API",
        "include_all_branches": False,
        "private": True,
    }


def test_github_repository_client_distinguishes_missing_repository_from_missing_template():
    def respond(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(404)

    client = GitHubRepositoryClient(
        GitHubRepositoryConfiguration(token="repository-token", api_url="https://api.github.test"),
        transport=httpx.MockTransport(respond),
    )

    assert client.get_repository("acme-platform", "checkout-api") is None
