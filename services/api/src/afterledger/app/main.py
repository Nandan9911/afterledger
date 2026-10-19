"""The HTTP service: FastAPI app, startup, errors and routers.

Run with `uvicorn afterledger.app.main:app --port 8080` (tools/dev.py api does this). Every route is
under /v1 and typed, so the OpenAPI document becomes the web app's TypeScript client.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .. import __version__
from ..logs import configure_logging
from ..runtime import Deps, build_deps
from . import demo
from .actions import BadInput, Conflict, NotFound, register_handlers
from .idempotency import IdempotencyMiddleware

log = logging.getLogger(__name__)


LAN = (r"http://(10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})"
       r"(:\d{1,5})?")


def lan_origins(origins: list[str]) -> str | None:
    """While the service runs for a web app on this computer (an origin on localhost), a phone on the same Wi-Fi
    opens that web app by the computer's private address, and its pages call the service from there. A deployed
    service, whose origins are its own web app's, takes no such pages."""
    return LAN if any(o.startswith(("http://localhost", "http://127.0.0.1")) for o in origins) else None


def create_app(deps: Deps | None = None, *, prepare_demo: bool | None = None) -> FastAPI:
    configure_logging((deps.settings if deps else _settings()).log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        d = deps or build_deps()
        app.state.deps = d
        register_handlers(d)
        from ..accounts import ensure_admin
        await ensure_admin(d)
        if prepare_demo if prepare_demo is not None else d.settings.seed_on_start:
            demo.start_preparing(d)
        yield
        task = d.extras.get("demo_task")
        if task is not None and not task.done():
            task.cancel()

    app = FastAPI(title="AfterLedger API", version=__version__, lifespan=lifespan,
                  description="Finds what a person left behind and prepares every claim, in any country. "
                              "Every family, bank and paper in the demo is fictional; the rules are real.")
    origins = [o.strip() for o in (deps.settings if deps else _settings()).cors_origins.split(",") if o.strip()]
    # a change asked for again after a dropped connection is answered, not done twice (inside CORS, so a repeated
    # answer carries its headers too)
    app.add_middleware(IdempotencyMiddleware)
    app.add_middleware(CORSMiddleware, allow_origins=origins or ["*"], allow_origin_regex=lan_origins(origins),
                       allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

    @app.exception_handler(NotFound)
    async def not_found(_: Request, exc: NotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc) or "Not found"})

    @app.exception_handler(Conflict)
    async def conflict(_: Request, exc: Conflict) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(BadInput)
    async def bad_input(_: Request, exc: BadInput) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(KeyError)
    async def key_error(_: Request, exc: KeyError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": f"Not found: {exc.args[0] if exc.args else ''}"})

    from .routers import auth, cases, challenge, desk, evaluation, internal, knowledge, meta, voice
    for r in (meta.router, auth.router, cases.router, knowledge.router, challenge.router, desk.router,
              evaluation.router, voice.router, internal.router):
        app.include_router(r)

    def openapi() -> dict:
        if app.openapi_schema is None:
            from fastapi.openapi.utils import get_openapi
            doc = get_openapi(title=app.title, version=app.version, description=app.description, routes=app.routes)
            app.openapi_schema = outputs_required(doc)
        return app.openapi_schema

    app.openapi = openapi  # type: ignore[method-assign]
    return app


def outputs_required(doc: dict) -> dict:
    """Every field of a response is always present (defaults included), so the client types say so; request
    bodies keep their optional fields optional."""
    schemas = doc.get("components", {}).get("schemas", {})

    def refs(node: object, found: set[str]) -> None:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
                name = ref.rsplit("/", 1)[1]
                if name not in found:
                    found.add(name)
                    refs(schemas.get(name, {}), found)
            for v in node.values():
                refs(v, found)
        elif isinstance(node, list):
            for v in node:
                refs(v, found)

    inputs: set[str] = set()
    for path in doc.get("paths", {}).values():
        for op in path.values():
            if isinstance(op, dict):
                refs(op.get("requestBody", {}), inputs)
    for name, schema in schemas.items():
        if name in inputs or name in ("HTTPValidationError", "ValidationError"):
            continue
        props = schema.get("properties")
        if props:
            schema["required"] = list(props.keys())
    return doc


def _settings() -> Any:
    from ..config import get_settings
    return get_settings()


app = create_app()
