from __future__ import annotations

import importlib
import os

import pytest

from claude_soma.api import main as main_module


def _cors_origins(monkeypatch: pytest.MonkeyPatch, **env: str) -> list[str]:
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    importlib.reload(main_module)
    app = main_module.create_app()
    for mw in app.middleware_stack.middlewares if hasattr(app, "middleware_stack") else []:
        pass
    app2 = main_module.create_app()
    from starlette.middleware.cors import CORSMiddleware as _StarletteCorsMW
    from fastapi.middleware.cors import CORSMiddleware
    for mw in app2.user_middleware:
        if mw.cls is CORSMiddleware:
            return list(mw.kwargs.get("allow_origins", []))
    return []


def _origins_from_env(monkeypatch: pytest.MonkeyPatch, **env: str) -> list[str]:
    for k in ("SOMA_DOMAIN", "HERMES_API_CORS_ORIGINS"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    app = main_module.create_app()
    from fastapi.middleware.cors import CORSMiddleware
    for mw in app.user_middleware:
        if mw.cls is CORSMiddleware:
            return list(mw.kwargs.get("allow_origins", []))
    return []


def test_cors_default_uses_soma_domain(monkeypatch: pytest.MonkeyPatch) -> None:
    origins = _origins_from_env(monkeypatch)
    assert "https://soma.mayankgupta.in" in origins
    assert "http://localhost:3000" in origins


def test_cors_soma_domain_env_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    origins = _origins_from_env(monkeypatch, SOMA_DOMAIN="dashboard.example.com")
    assert "https://dashboard.example.com" in origins
    assert "https://soma.mayankgupta.in" not in origins


def test_cors_hermes_env_overrides_soma_domain(monkeypatch: pytest.MonkeyPatch) -> None:
    origins = _origins_from_env(
        monkeypatch,
        SOMA_DOMAIN="dashboard.example.com",
        HERMES_API_CORS_ORIGINS="https://override.example.com",
    )
    assert "https://override.example.com" in origins
    assert "https://dashboard.example.com" not in origins
