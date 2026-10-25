import logging
import re
import secrets
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.logging import configure_logging
from app.core.telemetry import configure_telemetry
from app.db.session import engine

configure_logging()
logger = logging.getLogger("deployforge.api")
REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logger.info("application_starting", extra={"environment": settings.app_env})
    try:
        yield
    finally:
        if _app.state.telemetry_providers is not None:
            _app.state.telemetry_providers.shutdown()
        engine.dispose()
        logger.info("application_stopped")


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="DeployForge Enterprise control plane API.",
    docs_url=None if settings.app_env in {"production", "prod"} else "/docs",
    redoc_url=None if settings.app_env in {"production", "prod"} else "/redoc",
    openapi_url=None if settings.app_env in {"production", "prod"} else "/openapi.json",
    lifespan=lifespan,
)
app.add_middleware(
    SessionMiddleware,
    secret_key=(
        settings.session_secret.get_secret_value()
        if settings.session_secret
        else secrets.token_urlsafe(48)
    ),
    session_cookie="deployforge_oauth_state",
    max_age=600,
    same_site="lax",
    https_only=settings.session_cookie_secure,
)


@app.middleware("http")
async def request_logging(request: Request, call_next):
    supplied_request_id = request.headers.get("x-request-id", "")
    request_id = (
        supplied_request_id
        if REQUEST_ID_PATTERN.fullmatch(supplied_request_id)
        else str(uuid.uuid4())
    )
    start = time.perf_counter()
    response = await call_next(request)
    status_code = response.status_code if response.status_code is not None else 500
    elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cache-Control"] = "no-store"
    logger.info(
        "%s %s %s %.2fms",
        request.method,
        request.url.path,
        status_code,
        elapsed_ms,
        extra={"request_id": request_id},
    )
    return response


# Install tracing after app middleware so request logs execute inside the active span.
app.state.telemetry_providers = configure_telemetry(
    "deployforge-api", app=app, engine=engine
)


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        headers=exc.headers,
        content={
            "error": {
                "code": f"http_{exc.status_code}",
                "message": str(exc.detail),
            }
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    # Pydantic includes the rejected input in each error record by default. Keep
    # only structural metadata so credentials and tokens never echo to clients.
    details = [
        {
            "loc": list(error.get("loc", ())),
            "message": error.get("msg", "Invalid value"),
            "type": error.get("type", "value_error"),
        }
        for error in exc.errors()[:25]
    ]
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": "Request validation failed",
                "details": jsonable_encoder(details),
            }
        },
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled_application_error")
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "internal_error", "message": "An unexpected error occurred"}},
    )


@app.get("/", include_in_schema=False)
def root() -> dict[str, str]:
    return {"name": settings.app_name, "api": "/api/v1", "docs": "/docs"}


app.include_router(api_router, prefix="/api/v1")
