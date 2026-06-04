from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


def create_app() -> FastAPI:
    app = FastAPI(title="claude-soma-api", version="0.1.0")
    # SOMA_DOMAIN is the base domain (e.g. example.com). CORS allows the
    # soma.<domain> origin + localhost. If neither HERMES_API_CORS_ORIGINS nor
    # SOMA_DOMAIN is set, fall back to localhost-only — explicit deploys must
    # configure their domain via secrets.env to be reachable cross-origin.
    _soma_domain = os.environ.get("SOMA_DOMAIN", "").strip()
    _base = _soma_domain[5:] if _soma_domain.startswith("soma.") else _soma_domain
    if _base:
        _default_origin = f"https://soma.{_base},http://localhost:3000"
    else:
        _default_origin = "http://localhost:3000"
    origins = [o.strip() for o in os.environ.get(
        "HERMES_API_CORS_ORIGINS",
        _default_origin,
    ).split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware, allow_origins=origins, allow_credentials=True,
        allow_methods=["*"], allow_headers=["*"],
    )
    from claude_soma.api.routes import (
        healthz, public, projects, conversations, routines,
        usage, memory, logs, admin, admin_upload, admin_logs, events
    )
    app.include_router(healthz.router, prefix="/api")
    app.include_router(public.router, prefix="/api")
    app.include_router(projects.router, prefix="/api")
    app.include_router(conversations.router, prefix="/api")
    app.include_router(routines.router, prefix="/api")
    app.include_router(usage.router, prefix="/api")
    app.include_router(memory.router, prefix="/api")
    app.include_router(logs.router, prefix="/api")
    app.include_router(admin.router, prefix="/api")
    app.include_router(admin_upload.router, prefix="/api")
    app.include_router(admin_logs.router, prefix="/api")
    app.include_router(events.router, prefix="/api")
    return app


app = create_app()


def run() -> None:
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=9000)


if __name__ == "__main__":
    run()
