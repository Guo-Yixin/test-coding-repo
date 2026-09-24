from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health_endpoint() -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert "github_token_configured" in response.json()


def test_repository_validation_rejects_invalid_owner() -> None:
    response = client.get("/api/repository", params={"owner": "../bad", "repo": "repo"})

    assert response.status_code == 400
    assert response.json()["detail"] == "owner/repo 格式不正确"
