from sqlalchemy import inspect


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "CodeAtlas AI"}


def test_readiness(client):
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json()["database"] == "connected"


def test_openapi(client):
    response = client.get("/openapi.json")
    assert response.status_code == 200
    assert "/health" in response.json()["paths"]


def test_database_not_automatically_migrated(client):
    assert "projects" not in inspect(client.app.state.engine).get_table_names()
