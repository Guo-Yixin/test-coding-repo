from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import main
from app.main import app


client = TestClient(app)


def _fake_github_response(method: str, path: str, **kwargs: Any) -> Any:
    """按路径返回裁剪用不到的冗余字段，用于验证接口做了字段收敛。"""
    assert method == "GET"
    if path.endswith("/comments"):
        return [
            {
                "id": 1001,
                "body": "第一条评论",
                "created_at": "2024-01-02T03:04:05Z",
                "html_url": "https://github.com/Guo-Yixin/test-coding-repo/issues/7#issuecomment-1001",
                "user": {"login": "alice", "id": 1, "type": "User"},
                "reactions": {"total_count": 3},
            }
        ]
    return {
        "number": 7,
        "title": "示例 Issue",
        "state": "open",
        "html_url": "https://github.com/Guo-Yixin/test-coding-repo/issues/7",
        "comments": 1,
        "created_at": "2024-01-01T00:00:00Z",
        "user": {"login": "alice", "id": 1, "type": "User"},
        "labels": [{"name": "bug", "color": "ff0000"}],
        "body": "不应出现在响应中的正文字段",
    }


def test_health_endpoint() -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert "github_token_configured" in response.json()


def test_ready_endpoint() -> None:
    response = client.get("/api/ready")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "status": "ready"}
    # 就绪检查只表示“应用可服务”，不承载凭据信息。
    assert "github_token_configured" not in response.json()


def test_repository_validation_rejects_invalid_owner() -> None:
    response = client.get("/api/repository", params={"owner": "../bad", "repo": "repo"})

    assert response.status_code == 400
    assert response.json()["detail"] == "owner/repo 格式不正确"


def test_issue_context_rejects_invalid_owner() -> None:
    response = client.get("/api/issues/1/context", params={"owner": "../bad", "repo": "repo"})

    assert response.status_code == 400
    assert response.json()["detail"] == "owner/repo 格式不正确"


def test_issue_context_rejects_non_integer_number() -> None:
    response = client.get("/api/issues/abc/context", params={"owner": "Guo-Yixin", "repo": "test-coding-repo"})

    assert response.status_code == 422


def test_issue_context_trims_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "_github_request", _fake_github_response)

    response = client.get("/api/issues/7/context", params={"owner": "Guo-Yixin", "repo": "test-coding-repo"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["issue"] == {
        "number": 7,
        "title": "示例 Issue",
        "state": "open",
        "html_url": "https://github.com/Guo-Yixin/test-coding-repo/issues/7",
        "user": "alice",
        "labels": ["bug"],
        "comments": 1,
        "created_at": "2024-01-01T00:00:00Z",
    }
    assert payload["comments"] == [
        {
            "id": 1001,
            "user": "alice",
            "body": "第一条评论",
            "created_at": "2024-01-02T03:04:05Z",
            "html_url": "https://github.com/Guo-Yixin/test-coding-repo/issues/7#issuecomment-1001",
        }
    ]


def test_issue_context_propagates_github_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise_not_found(*args: Any, **kwargs: Any) -> Any:
        raise HTTPException(status_code=404, detail="GitHub API 请求失败: Not Found")

    monkeypatch.setattr(main, "_github_request", _raise_not_found)

    response = client.get("/api/issues/404/context", params={"owner": "Guo-Yixin", "repo": "test-coding-repo"})

    assert response.status_code == 404
    assert response.json()["detail"].startswith("GitHub API 请求失败")
