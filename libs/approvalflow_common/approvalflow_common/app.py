"""Application factory shared by every service.

Each service calls `create_app("name")` to get a FastAPI app pre-wired with
structured logging, the correlation-id middleware, and a health check — so the
cross-cutting concerns live in one place (separation of concerns, M15).
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import get_settings
from .logging import configure_logging, get_logger
from .middleware import CorrelationIdMiddleware


def create_app(service_name: str, **kwargs) -> FastAPI:
    settings = get_settings()
    configure_logging(service_name, settings.log_level)
    log = get_logger(service_name)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        log.info("service started", extra={"event": "startup", "svc": service_name})
        yield
        log.info("service stopping", extra={"event": "shutdown", "svc": service_name})

    app = FastAPI(title=f"ApprovalFlow · {service_name}", lifespan=lifespan, **kwargs)
    app.add_middleware(CorrelationIdMiddleware)

    @app.get("/health", tags=["meta"])
    def health() -> dict:
        return {"status": "ok", "service": service_name}

    return app
