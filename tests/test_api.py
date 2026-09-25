from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import main
from app.main import app


client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_cache() -> Any:
    """每个用例从空缓存开始，避免计数器与缓存条目跨用例串扰。"""
    main.CACHE_TTL_SECONDS = 45
    main._cache_clear()
    for name in ("hits", "misses", "stores", "evictions"):
        main._cache_stats[name] = 0
    yield
    main.CACHE_TTL_SECONDS = 45
    main._cache_clear()
    for name in ("hits", "misses", "stores", "evictions"):
        main._cache_stats[name] = 0


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


LIST_QUERY = {"owner": "Guo-Yixin", "repo": "test-coding-repo", "state": "open", "per_page": 100}


def _fake_list_response(items: list[dict[str, Any]]) -> Any:
    calls: list[tuple[str, str]] = []

    def _respond(method: str, path: str, **kwargs: Any) -> Any:
        calls.append((method, path))
        return items

    _respond.calls = calls  # type: ignore[attr-defined]
    return _respond


def _make_pull(number: int, title: str) -> dict[str, Any]:
    return {
        "number": number,
        "title": title,
        "state": "open",
        "draft": False,
        "html_url": f"https://github.com/Guo-Yixin/test-coding-repo/pull/{number}",
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-02T00:00:00Z",
        "user": {"login": "alice", "id": 1, "type": "User"},
        "head": {"ref": "feature", "sha": "abc123", "repo": {"full_name": "Guo-Yixin/test-coding-repo"}},
        "base": {"ref": "main", "sha": "def456"},
        "body": "不应出现在响应中的正文",
        "_links": {"self": {"href": "https://api.github.com"}},
    }


def _make_issue(number: int, title: str, *, is_pull: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "number": number,
        "title": title,
        "state": "open",
        "html_url": f"https://github.com/Guo-Yixin/test-coding-repo/issues/{number}",
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-02T00:00:00Z",
        "user": {"login": "alice", "id": 1, "type": "User"},
        "labels": [{"name": "bug", "color": "ff0000"}],
        "comments": 2,
        "assignee": {"login": "bob"},
        "body": "不应出现在响应中的正文",
    }
    if is_pull:
        payload["pull_request"] = {"url": "https://api.github.com/repos/Guo-Yixin/test-coding-repo/pulls/9"}
    return payload


def test_pull_list_trims_fields(monkeypatch: pytest.MonkeyPatch, _reset_cache: Any) -> None:
    responder = _fake_list_response([_make_pull(3, "缓存层")])
    monkeypatch.setattr(main, "_github_request", responder)

    response = client.get("/api/pulls", params=LIST_QUERY)

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "count": 1,
        "state": "open",
        "pull_requests": [
            {
                "number": 3,
                "title": "缓存层",
                "state": "open",
                "draft": False,
                "user": "alice",
                "head": "feature",
                "base": "main",
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-02T00:00:00Z",
                "html_url": "https://github.com/Guo-Yixin/test-coding-repo/pull/3",
            }
        ],
    }
    assert "body" not in payload["pull_requests"][0]
    assert "_links" not in payload["pull_requests"][0]


def test_issue_list_filters_pull_requests(monkeypatch: pytest.MonkeyPatch, _reset_cache: Any) -> None:
    responder = _fake_list_response([_make_issue(1, "真实 Issue"), _make_issue(9, "其实是 PR", is_pull=True)])
    monkeypatch.setattr(main, "_github_request", responder)

    response = client.get("/api/issues", params=LIST_QUERY)

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert [item["number"] for item in payload["issues"]] == [1]
    first = payload["issues"][0]
    assert first["labels"] == ["bug"]
    assert first["comments"] == 2
    assert "body" not in first
    # GitHub 的 issues 接口会混入 PR，这里断言确实过滤掉了带 pull_request 的元素，
    # 且请求打向的是 issues 列表路径。
    method, path = responder.calls[0]  # type: ignore[attr-defined]
    assert (method, path) == ("GET", "/repos/Guo-Yixin/test-coding-repo/issues")
    assert len(responder.calls) == 1  # type: ignore[attr-defined]


def test_list_rejects_invalid_state() -> None:
    for endpoint in ("/api/pulls", "/api/issues"):
        response = client.get(endpoint, params={"owner": "Guo-Yixin", "repo": "test-coding-repo", "state": "merged"})

        assert response.status_code == 400
        assert response.json()["detail"] == "state 只能是 open/closed/all"


def test_list_rejects_out_of_range_per_page() -> None:
    response = client.get("/api/pulls", params={**LIST_QUERY, "per_page": 101})

    assert response.status_code == 400
    assert response.json()["detail"] == "per_page 必须在 1 到 100 之间"


def _wire_fake_http(monkeypatch: pytest.MonkeyPatch, body: Any = None) -> dict[str, list[Any]]:
    """替换 httpx.Client，让真实 `_github_request`（含缓存逻辑）跑完全程，但不发网络请求。

    `body` 为 None 时复刻 GitHub 的真实形状：评论路径返回列表，其余返回对象。
    """
    upstream: dict[str, list[Any]] = {"calls": []}

    class _FakeResponse:
        status_code = 200
        content = b"1"
        text = "{}"

        def json(self) -> Any:
            if body is not None:
                return body
            method, url, _params = upstream["calls"][-1]
            return [{"id": 1001, "body": "第一条评论", "user": {"login": "alice"}}] if url.endswith("/comments") else {
                "number": 7,
                "title": "示例 Issue",
                "state": "open",
                "comments": 1,
                "user": {"login": "alice"},
                "labels": [{"name": "bug"}],
            }

    class _FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def __enter__(self) -> "_FakeClient":
            return self

        def __exit__(self, *args: Any) -> bool:
            return False

        def request(self, method: str, url: str, params: Any = None, json: Any = None) -> "_FakeResponse":
            upstream["calls"].append((method, url, params))
            return _FakeResponse()

    monkeypatch.setattr(main.httpx, "Client", _FakeClient)
    return upstream


def test_cache_reuses_second_request(monkeypatch: pytest.MonkeyPatch, _reset_cache: Any) -> None:
    """两次相同列表请求只产生一次上游调用，第二次由缓存承接。"""
    upstream = _wire_fake_http(monkeypatch, [{"number": 3, "title": "缓存层"}])

    first = client.get("/api/pulls", params=LIST_QUERY)
    second = client.get("/api/pulls", params=LIST_QUERY)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(upstream["calls"]) == 1
    assert main._cache_stats["hits"] == 1
    assert main._cache_stats["misses"] == 1
    assert main._cache_stats["stores"] == 1


def test_cache_is_not_used_by_write_requests(monkeypatch: pytest.MonkeyPatch, _reset_cache: Any) -> None:
    """POST 永不缓存，绝不因为缓存而跳过真实的创建请求。"""
    upstream = _wire_fake_http(monkeypatch, {"html_url": "https://example.com", "number": 5})

    payload = {"owner": "Guo-Yixin", "repo": "test-coding-repo", "title": "新 Issue"}
    client.post("/api/issues", json=payload)
    client.post("/api/issues", json=payload)

    assert len(upstream["calls"]) == 2
    assert main._cache_stats["entries"] == 0


def test_cache_reuse_is_observable_in_context_endpoint(monkeypatch: pytest.MonkeyPatch, _reset_cache: Any) -> None:
    """同一上下文连读两次：第二次完全走缓存，上游调用次数不再增长。"""
    upstream = _wire_fake_http(monkeypatch)

    first = client.get("/api/issues/7/context", params={"owner": "Guo-Yixin", "repo": "test-coding-repo"})
    after_first = len(upstream["calls"])
    second = client.get("/api/issues/7/context", params={"owner": "Guo-Yixin", "repo": "test-coding-repo"})

    assert first.status_code == second.status_code == 200
    # issue 本体与评论接口各一次上游调用，重复请求不再产生调用。
    assert after_first == 2
    assert len(upstream["calls"]) == after_first
    assert main._cache_stats["hits"] == 2


def test_cache_is_disabled_when_ttl_zero(monkeypatch: pytest.MonkeyPatch, _reset_cache: Any) -> None:
    upstream = _wire_fake_http(monkeypatch, [{"number": 3, "title": "缓存层"}])
    main.CACHE_TTL_SECONDS = 0

    client.get("/api/pulls", params=LIST_QUERY)
    client.get("/api/pulls", params=LIST_QUERY)

    # TTL=0 表示关闭缓存：每次都真实打到上游，且不写入任何条目。
    assert len(upstream["calls"]) == 2
    assert main._cache_stats["hits"] == 0
    assert main._cache_stats["entries"] == 0


def test_cache_key_separates_query_params(monkeypatch: pytest.MonkeyPatch, _reset_cache: Any) -> None:
    upstream = _wire_fake_http(monkeypatch, [{"number": 3, "title": "缓存层"}])

    client.get("/api/pulls", params=LIST_QUERY)
    client.get("/api/pulls", params={**LIST_QUERY, "state": "closed"})
    client.get("/api/pulls", params=LIST_QUERY)

    # 两个筛选条件各打一次上游，第三次命中缓存，说明 key 未互相污染。
    assert len(upstream["calls"]) == 2
    assert main._cache_stats["hits"] == 1
    assert main._cache_stats["entries"] == 2


def test_health_reports_cache_observation(monkeypatch: pytest.MonkeyPatch, _reset_cache: Any) -> None:
    monkeypatch.setattr(main, "_github_request", _fake_list_response([_make_pull(3, "缓存层")]))

    response = client.get("/api/health")

    assert response.status_code == 200
    cache = response.json()["cache"]
    assert cache["ttl_seconds"] == 45
    assert cache["max_entries"] == 128
    assert cache["enabled"] is True
    # health 只暴露非敏感的缓存配置与统计，不含主机本地路径或凭据。
    assert "GITHUB_TOKEN" not in response.text


def test_github_error_is_not_cached(monkeypatch: pytest.MonkeyPatch, _reset_cache: Any) -> None:
    calls: list[int] = []

    def _raise(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        raise HTTPException(status_code=403, detail="GitHub API 请求失败: rate limited")

    monkeypatch.setattr(main, "_github_request", _raise)

    first = client.get("/api/pulls", params=LIST_QUERY)
    second = client.get("/api/pulls", params=LIST_QUERY)

    # 失败结果不写入缓存，两次都真实打到上游，避免把限流错误“冻住”。
    assert first.status_code == second.status_code == 403
    assert len(calls) == 2
    assert main._cache_stats["entries"] == 0


def test_cache_evicts_least_recently_used(monkeypatch: pytest.MonkeyPatch, _reset_cache: Any) -> None:
    monkeypatch.setattr(main, "CACHE_MAX_ENTRIES", 2)

    main._cache_store("GET a", {"v": 1})
    main._cache_store("GET b", {"v": 2})
    main._cache_store("GET c", {"v": 3})

    assert len(main._cache) == 2
    assert main._cache_stats["evictions"] == 1
    assert "GET a" not in main._cache
