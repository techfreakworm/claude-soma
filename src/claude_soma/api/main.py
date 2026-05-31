from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


def create_app() -> FastAPI:
    app = FastAPI(title="claude-soma-api", version="0.1.0")
    _soma_domain = os.environ.get("SOMA_DOMAIN", "soma.mayankgupta.in")
    origins = [o.strip() for o in os.environ.get(
        "HERMES_API_CORS_ORIGINS",
        f"https://{_soma_domain},http://localhost:3000",
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
