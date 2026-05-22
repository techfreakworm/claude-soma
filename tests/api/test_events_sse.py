from claude_soma.api.main import create_app


def test_events_route_registered() -> None:
    app = create_app()
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/api/events" in paths
