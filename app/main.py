"""FastAPI application: routers, exception mapping, OpenAPI metadata."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api import audit, orders, policies, quotes
from app.exceptions import AuthorizationError, InvalidTransitionError, NotFoundError

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

_STATUS_FOR = {
    NotFoundError: 404,
    AuthorizationError: 403,
    InvalidTransitionError: 409,
}


def _register(exc_type: type, status_code: int) -> None:
    @app.exception_handler(exc_type)
    async def handler(request: Request, exc: Exception, _code=status_code):
        return JSONResponse(status_code=_code, content={"detail": str(exc)})


for exc_type, code in _STATUS_FOR.items():
    _register(exc_type, code)


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}
