from fastapi.testclient import TestClient

from notebook_launcher.app import create_app
from notebook_launcher.config import Settings


def test_health_is_loopback_configured(tmp_path):
    settings = Settings(root=tmp_path, host="127.0.0.1", port=8080)
    client = TestClient(create_app(settings))
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["host"] == "127.0.0.1"
