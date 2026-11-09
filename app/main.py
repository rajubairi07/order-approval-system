"""FastAPI application: routers, exception mapping, OpenAPI metadata."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api import audit, orders, policies, quotes
from app.exceptions import (
    AuthorizationError,
    InvalidActionError,
    InvalidTransitionError,
    NotFoundError,
)

app = FastAPI(
    title="Agentic Order & Quote Approval Management System",
    description=(
        "Multi-agent (LangGraph) approval workflows for purchase orders and sales "
        "quotes. Business rules live in the database; agents access them only "
        "through tools; decisions are structured, validated and fully audited."
    ),
    version="1.0.0",
)

app.include_router(orders.router)
app.include_router(quotes.router)
app.include_router(policies.router)
app.include_router(audit.router)

# Only typed domain errors map to client-facing codes. Deliberately no
# blanket ValueError handler: pydantic's ValidationError subclasses ValueError,
# and server misconfiguration (e.g. a bad LLM_PROVIDER) must surface as 5xx,
# not a client-blaming 400.
_STATUS_FOR = {
    NotFoundError: 404,
    AuthorizationError: 403,
    InvalidTransitionError: 409,
    InvalidActionError: 400,
}


def _register(exc_type: type, status_code: int) -> None:
    @app.exception_handler(exc_type)
    async def handler(request: Request, exc: Exception, _code=status_code):
        return JSONResponse(status_code=_code, content={"detail": str(exc)})


for exc_type, code in _STATUS_FOR.items():
    _register(exc_type, code)


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}
