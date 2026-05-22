from fastapi.testclient import TestClient

from claude_soma.api.main import create_app

HEADERS = {"X-GitHub-Handle": "techfreakworm"}


def test_events_returns_event_stream_content_type() -> None:
    app = create_app()
    client = TestClient(app)
    # TestClient doesn't stream interactively, so we just verify the route
    # exists and responds with the SSE content-type header.
    with client.stream("GET", "/api/events", headers=HEADERS) as r:
        assert r.status_code == 200
        assert "text/event-stream" in r.headers.get("content-type", "")
