"""Interactive API reference and schema routes."""

import httpx
import pytest

from server.startup import app


@pytest.mark.unit
@pytest.mark.asyncio
async def test_api_docs_theme_and_schema_routes():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        docs = await client.get("/api/docs")
        schema = await client.get("/api/openapi.json")
        redoc = await client.get("/api/redoc")

    assert docs.status_code == 200
    assert 'id="docs-theme-toggle"' in docs.text
    assert 'data-docs-theme="dark"' in docs.text
    assert "ground-station-api-docs-theme" in docs.text
    assert "/api/openapi.json" in docs.text
    assert schema.status_code == 200
    operations = schema.json()["paths"]
    assert set(operations) == {
        "/api/v1/me",
        "/api/v1/station",
        "/api/v1/status",
        "/api/v1/events",
        "/api/v1/observations",
        "/api/v1/observations/upcoming",
        "/api/v1/observations/{observation_id}",
        "/api/v1/satellites",
        "/api/v1/satellites/monitored",
        "/api/v1/satellites/{norad_id}",
        "/api/v1/artifacts",
        "/api/v1/artifacts/{artifact_id}",
    }
    for methods in operations.values():
        for operation in methods.values():
            assert operation["summary"]
            assert operation["description"]
            assert {"HTTPBearer": []} in operation["security"]
    assert redoc.status_code == 200

    # Hidden browser routes still exist; they simply do not appear in the public schema.
    route_paths = {route.path for route in app.routes}
    assert "/api/auth/login" in route_paths
    assert "/api/database/restore" in route_paths
    assert "/api/decoded/{foldername}/download" in route_paths
