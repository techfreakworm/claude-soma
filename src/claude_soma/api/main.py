from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from claude_soma.api.routes import healthz, public


def create_app() -> FastAPI:
    app = FastAPI(title="claude-soma-api", version="0.1.0")

    origins = [o.strip() for o in os.environ.get(
        "HERMES_API_CORS_ORIGINS",
        "https://claude.mayankgupta.in,http://localhost:3000",
    ).split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware, allow_origins=origins, allow_credentials=True,
        allow_methods=["*"], allow_headers=["*"],
    )

    app.include_router(healthz.router, prefix="/api")
    app.include_router(public.router, prefix="/api")
    return app


app = create_app()


def run() -> None:
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=9000)


if __name__ == "__main__":
    run()
