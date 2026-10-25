from fastapi import APIRouter

from app.api.v1.endpoints import (
    audit,
    auth,
    build_runs,
    catalog,
    deployments,
    gitops,
    health,
    infrastructure,
    observability,
    policies,
    workspaces,
)

api_router = APIRouter()
api_router.include_router(health.router, tags=["system"])
api_router.include_router(auth.router, prefix="/auth", tags=["identity"])
api_router.include_router(workspaces.router, prefix="/workspaces", tags=["workspaces"])
api_router.include_router(catalog.router, tags=["service catalog"])
api_router.include_router(deployments.router, tags=["deployments"])
api_router.include_router(build_runs.router, tags=["builds and supply chain"])
api_router.include_router(gitops.router, tags=["kubernetes and GitOps"])
api_router.include_router(infrastructure.router, tags=["environments and infrastructure"])
api_router.include_router(policies.router, tags=["policy as code"])
api_router.include_router(observability.router, tags=["observability and SRE"])
api_router.include_router(audit.router, prefix="/audit-logs", tags=["audit"])
