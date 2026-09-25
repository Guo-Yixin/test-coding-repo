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


def _fake_github_response_with_headers(
    monkeypatch: pytest.MonkeyPatch, *, link: str | None = None
) -> None:
    """替换 `_github_request_with_headers`，让评论分页路径也能离线断言 Link 行为。

    `_github_request_with_headers` 的返回值是 `(body, link)` 二元组，因此评论翻页相关
    用例必须替换这个函数，而不是 `_github_request`。正文数据仍复用
    `_fake_github_response`，内部只兜住 `_github_request` 与 `_parse_link_header`。
    """
    monkeypatch.setattr(
        main,
        "_github_request_with_headers",
        lambda method, path, **kwargs: (_fake_github_response(method, path, **kwargs), link),
    )
    monkeypatch.setattr(main, "_github_request", _fake_github_response)


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
    _fake_github_response_with_headers(monkeypatch)

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
    # 未传分页参数时沿用改动前行为：第 1 页、每页 100 条，且上游未声明下一页。
    assert payload["comments_page"] == 1
    assert payload["comments_per_page"] == 100
    assert payload["comments_has_more"] is False
    assert payload["comments_next_page"] is None


def test_issue_context_propagates_github_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise_not_found(*args: Any, **kwargs: Any) -> Any:
        raise HTTPException(status_code=404, detail="GitHub API 请求失败: Not Found")

    monkeypatch.setattr(main, "_github_request", _raise_not_found)

    response = client.get("/api/issues/404/context", params={"owner": "Guo-Yixin", "repo": "test-coding-repo"})

    assert response.status_code == 404
    assert response.json()["detail"].startswith("GitHub API 请求失败")


LIST_QUERY = {"owner": "Guo-Yixin", "repo": "test-coding-repo", "state": "open", "per_page": 100}


def _fake_list_response(items: list[dict[str, Any]], *, link: str | None = None) -> Any:
    """替换 `_github_request_with_headers`：记录上游调用并回放 `Link` 头。"""
    calls: list[tuple[str, str, Any]] = []

    def _respond(method: str, path: str, *, params: Any = None, **kwargs: Any) -> Any:
        calls.append((method, path, params))
        return items, link

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
    monkeypatch.setattr(main, "_github_request_with_headers", responder)

    response = client.get("/api/pulls", params=LIST_QUERY)

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "count": 1,
        "state": "open",
        "per_page": 100,
        "page": 1,
        "has_more": False,
        "next_page": None,
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
    # 未显式传 page 时不应给上游带上该参数，避免无谓改变请求形状。
    assert "page" not in responder.calls[0][2]  # type: ignore[attr-defined]


def test_issue_list_filters_pull_requests(monkeypatch: pytest.MonkeyPatch, _reset_cache: Any) -> None:
    responder = _fake_list_response([_make_issue(1, "真实 Issue"), _make_issue(9, "其实是 PR", is_pull=True)])
    monkeypatch.setattr(main, "_github_request_with_headers", responder)

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
    method, path, _params = responder.calls[0]  # type: ignore[attr-defined]
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


# --- Link 头解析（纯函数，不经过 HTTP）-------------------------------------


def test_parse_link_header_returns_next_page() -> None:
    link = (
        '<https://api.github.com/repos/o/r/issues?page=3&per_page=30>; rel="next", '
        '<https://api.github.com/repos/o/r/issues?page=9&per_page=30>; rel="last", '
        '<https://api.github.com/repos/o/r/issues?page=1&per_page=30>; rel="first"'
    )

    has_more, next_page = main._parse_link_header(link)

    assert has_more is True
    assert next_page == 3


def test_parse_link_header_without_next() -> None:
    link = (
        '<https://api.github.com/repos/o/r/issues?page=1&per_page=30>; rel="first", '
        '<https://api.github.com/repos/o/r/issues?page=1&per_page=30>; rel="prev"'
    )

    assert main._parse_link_header(link) == (False, None)


def test_parse_link_header_handles_missing_value() -> None:
    assert main._parse_link_header(None) == (False, None)
    assert main._parse_link_header("") == (False, None)


def test_parse_link_header_never_raises_on_garbage() -> None:
    garbage_values = [
        '<https://api.github.com/repos/o/r/issues?page=2&per_page=30>; rel=next", '  # 缺右反引号
        '<https://api.github.com/repos/o/r/issues?page=abc>; rel="next"',  # page 非数字
        '<https://api.github.com/repos/o/r/issues?page=>; rel="next"',  # page 为空
        '<https://api.github.com/repos/o/r/issues>; rel="next"',  # 无 page 参数
        'rel="next"',  # 没有 URL
    ]

    for value in garbage_values:
        assert main._parse_link_header(value) == (False, None)


# --- 列表翻页 ---------------------------------------------------------------


def test_pull_list_passes_page_and_reports_has_more(monkeypatch: pytest.MonkeyPatch, _reset_cache: Any) -> None:
    link = '<https://api.github.com/repos/o/r/pulls?page=3&per_page=2>; rel="next"'
    responder = _fake_list_response([_make_pull(3, "第二页")], link=link)
    monkeypatch.setattr(main, "_github_request_with_headers", responder)

    response = client.get("/api/pulls", params={**LIST_QUERY, "per_page": 2, "page": 2})

    assert response.status_code == 200
    payload = response.json()
    assert payload["page"] == 2
    assert payload["per_page"] == 2
    assert payload["has_more"] is True
    assert payload["next_page"] == 3
    method, path, params = responder.calls[0]  # type: ignore[attr-defined]
    assert (method, path) == ("GET", "/repos/Guo-Yixin/test-coding-repo/pulls")
    assert params["page"] == 2
    assert params["per_page"] == 2


def test_list_omits_page_when_not_requested(monkeypatch: pytest.MonkeyPatch, _reset_cache: Any) -> None:
    responder = _fake_list_response([_make_pull(3, "首页")])
    monkeypatch.setattr(main, "_github_request_with_headers", responder)

    client.get("/api/pulls", params=LIST_QUERY)

    _method, _path, params = responder.calls[0]  # type: ignore[attr-defined]
    # 未显式传 page 时不向上游注入 page，保持与改动前一致的请求参数。
    assert "page" not in params


def test_has_more_is_false_when_no_link_even_if_page_is_full(
    monkeypatch: pytest.MonkeyPatch, _reset_cache: Any
) -> None:
    """核心防线：条数等于 per_page 但没有 Link: rel="next" 时不能猜成“还有下一页”。"""
    responder = _fake_list_response([_make_pull(n, f"第 {n} 条") for n in (1, 2, 3)])
    monkeypatch.setattr(main, "_github_request_with_headers", responder)

    response = client.get("/api/pulls", params={**LIST_QUERY, "per_page": 3})

    payload = response.json()
    assert payload["count"] == 3
    assert payload["has_more"] is False
    assert payload["next_page"] is None


def test_cursor_is_translated_to_page(monkeypatch: pytest.MonkeyPatch, _reset_cache: Any) -> None:
    responder = _fake_list_response([_make_pull(3, "第二页")])
    monkeypatch.setattr(main, "_github_request_with_headers", responder)

    response = client.get("/api/issues", params={**LIST_QUERY, "cursor": 2})

    assert response.status_code == 200
    assert response.json()["page"] == 2
    _method, _path, params = responder.calls[0]  # type: ignore[attr-defined]
    assert params["page"] == 2


def test_cursor_conflicts_with_page() -> None:
    response = client.get("/api/pulls", params={**LIST_QUERY, "page": 2, "cursor": 3})

    assert response.status_code == 400
    assert response.json()["detail"] == "cursor 与 page 不能同时使用"


def test_cursor_must_be_positive_integer() -> None:
    for value in (0, -1):
        response = client.get("/api/pulls", params={**LIST_QUERY, "cursor": value})

        assert response.status_code == 400
        assert response.json()["detail"] == "cursor 必须是正整数"

    # 非整数值由 FastAPI 的查询参数校验直接拒绝。
    assert client.get("/api/pulls", params={**LIST_QUERY, "cursor": "abc"}).status_code == 422


def test_issue_list_still_filters_with_pagination(monkeypatch: pytest.MonkeyPatch, _reset_cache: Any) -> None:
    link = '<https://api.github.com/repos/o/r/issues?page=2&per_page=2>; rel="next"'
    responder = _fake_list_response(
        [_make_issue(1, "真实 Issue"), _make_issue(9, "其实是 PR", is_pull=True)], link=link
    )
    monkeypatch.setattr(main, "_github_request_with_headers", responder)

    response = client.get("/api/issues", params={**LIST_QUERY, "per_page": 2})

    payload = response.json()
    # count 是「本页过滤后」的条数，has_more 则来自上游 Link，二者不同源。
    assert payload["count"] == 1
    assert payload["has_more"] is True
    assert payload["next_page"] == 2


# --- 评论翻页 ---------------------------------------------------------------


def test_issue_context_comments_pagination(monkeypatch: pytest.MonkeyPatch, _reset_cache: Any) -> None:
    captured: list[Any] = []
    link = '<https://api.github.com/repos/o/r/issues/7/comments?page=3&per_page=2>; rel="next"'

    def _respond(method: str, path: str, *, params: Any = None, **kwargs: Any) -> Any:
        captured.append((path, params))
        return _fake_github_response(method, path), (link if path.endswith("/comments") else None)

    monkeypatch.setattr(main, "_github_request_with_headers", _respond)
    monkeypatch.setattr(main, "_github_request", _fake_github_response)

    response = client.get(
        "/api/issues/7/context",
        params={"owner": "Guo-Yixin", "repo": "test-coding-repo", "comments_page": 2, "comments_per_page": 2},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["comments_page"] == 2
    assert payload["comments_per_page"] == 2
    assert payload["comments_has_more"] is True
    assert payload["comments_next_page"] == 3
    comment_params = [params for path, params in captured if path.endswith("/comments")]
    assert comment_params == [{"page": 2, "per_page": 2}]


def test_issue_context_defaults_comments_to_first_page(monkeypatch: pytest.MonkeyPatch, _reset_cache: Any) -> None:
    captured: list[Any] = []

    def _respond(method: str, path: str, *, params: Any = None, **kwargs: Any) -> Any:
        captured.append((path, params))
        return _fake_github_response(method, path), None

    monkeypatch.setattr(main, "_github_request_with_headers", _respond)
    monkeypatch.setattr(main, "_github_request", _fake_github_response)

    response = client.get("/api/issues/7/context", params={"owner": "Guo-Yixin", "repo": "test-coding-repo"})

    payload = response.json()
    assert payload["comments_page"] == 1
    assert payload["comments_per_page"] == 100
    assert payload["comments_has_more"] is False
    # 兼容性：不传参数时上游仍然收到 per_page=100，与改动前行为一致。
    assert [params for path, params in captured if path.endswith("/comments")] == [{"page": 1, "per_page": 100}]


def test_issue_context_rejects_out_of_range_comments_per_page() -> None:
    response = client.get(
        "/api/issues/7/context",
        params={"owner": "Guo-Yixin", "repo": "test-coding-repo", "comments_per_page": 101},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "comments_per_page 必须在 1 到 100 之间"


def test_pull_context_comments_pagination(monkeypatch: pytest.MonkeyPatch, _reset_cache: Any) -> None:
    captured: list[Any] = []
    link = '<https://api.github.com/repos/o/r/issues/3/comments?page=2&per_page=5>; rel="next"'

    def _respond(method: str, path: str, *, params: Any = None, **kwargs: Any) -> Any:
        captured.append((path, params))
        return _fake_github_response(method, path), (link if path.endswith("/comments") else None)

    monkeypatch.setattr(main, "_github_request_with_headers", _respond)
    monkeypatch.setattr(main, "_github_request", _fake_github_response)

    response = client.get(
        "/api/pulls/3/context",
        params={"owner": "Guo-Yixin", "repo": "test-coding-repo", "comments_page": 1, "comments_per_page": 5},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["comments_per_page"] == 5
    assert payload["comments_has_more"] is True
    assert payload["comments_next_page"] == 2
    assert [params for path, params in captured if path.endswith("/comments")] == [{"page": 1, "per_page": 5}]


def _wire_fake_http(
    monkeypatch: pytest.MonkeyPatch, body: Any = None, *, link: str | None = None
) -> dict[str, list[Any]]:
    """替换 httpx.Client，让真实 `_github_request`（含缓存逻辑）跑完全程，但不发网络请求。

    `body` 为 None 时复刻 GitHub 的真实形状：评论路径返回列表，其余返回对象。
    `link` 用于模拟上游 `Link` 响应头，供 `_github_request_with_headers` 读取。
    """
    upstream: dict[str, list[Any]] = {"calls": []}

    class _FakeResponse:
        status_code = 200
        content = b"1"
        text = "{}"

        def __init__(self) -> None:
            self.headers = {"Link": link} if link is not None else {}

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
    # 列表与评论读取走 `_github_request_with_headers`，它绕过缓存但同样需要绕开网络，
    # 因此这里复用同一份假客户端拼出 (body, link) 形态。
    def _with_headers(method: str, path: str, *, params: Any = None, **kwargs: Any) -> tuple[Any, str | None]:
        with _FakeClient() as fake_client:
            response = fake_client.request(method, f"{main.GITHUB_API_BASE_URL}{path}", params=params)
        return (response.json() if response.content else {}), response.headers.get("Link")

    monkeypatch.setattr(main, "_github_request_with_headers", _with_headers)
    return upstream


def test_cache_reuses_second_request(monkeypatch: pytest.MonkeyPatch, _reset_cache: Any) -> None:
    """两次相同详情请求只产生一次上游调用，第二次由缓存承接。"""
    upstream = _wire_fake_http(monkeypatch, {"number": 3, "title": "缓存层"})

    first = client.get("/api/repository", params={"owner": "Guo-Yixin", "repo": "test-coding-repo"})
    second = client.get("/api/repository", params={"owner": "Guo-Yixin", "repo": "test-coding-repo"})

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(upstream["calls"]) == 1
    assert main._cache_stats["hits"] == 1
    assert main._cache_stats["misses"] == 1
    assert main._cache_stats["stores"] == 1


def test_list_endpoints_bypass_cache(monkeypatch: pytest.MonkeyPatch, _reset_cache: Any) -> None:
    """列表接口走 `_github_request_with_headers`：拿到 Link 的代价是真实打到上游。"""
    upstream = _wire_fake_http(monkeypatch, [{"number": 3, "title": "缓存层"}])

    client.get("/api/pulls", params=LIST_QUERY)
    client.get("/api/pulls", params=LIST_QUERY)

    assert len(upstream["calls"]) == 2
    assert main._cache_stats["stores"] == 0
    assert main._cache_stats["entries"] == 0


def test_cache_is_not_used_by_write_requests(monkeypatch: pytest.MonkeyPatch, _reset_cache: Any) -> None:
    """POST 永不缓存，绝不因为缓存而跳过真实的创建请求。"""
    upstream = _wire_fake_http(monkeypatch, {"html_url": "https://example.com", "number": 5})

    payload = {"owner": "Guo-Yixin", "repo": "test-coding-repo", "title": "新 Issue"}
    client.post("/api/issues", json=payload)
    client.post("/api/issues", json=payload)

    assert len(upstream["calls"]) == 2
    assert main._cache_stats["entries"] == 0


def test_cache_reuse_is_observable_in_context_endpoint(monkeypatch: pytest.MonkeyPatch, _reset_cache: Any) -> None:
    """issue 本体走缓存，评论因为要读 Link 每次真实打到上游。"""
    upstream = _wire_fake_http(monkeypatch)

    first = client.get("/api/issues/7/context", params={"owner": "Guo-Yixin", "repo": "test-coding-repo"})
    after_first = len(upstream["calls"])
    second = client.get("/api/issues/7/context", params={"owner": "Guo-Yixin", "repo": "test-coding-repo"})

    assert first.status_code == second.status_code == 200
    # 首次：issue 本体 + 评论接口共 2 次上游调用。
    assert after_first == 2
    # 第二次只有评论接口再打一次上游；issue 本体由缓存承接。
    assert len(upstream["calls"]) == after_first + 1
    assert main._cache_stats["hits"] == 1


def test_cache_is_disabled_when_ttl_zero(monkeypatch: pytest.MonkeyPatch, _reset_cache: Any) -> None:
    upstream = _wire_fake_http(monkeypatch, {"number": 3, "title": "缓存层"})
    main.CACHE_TTL_SECONDS = 0

    client.get("/api/repository", params={"owner": "Guo-Yixin", "repo": "test-coding-repo"})
    client.get("/api/repository", params={"owner": "Guo-Yixin", "repo": "test-coding-repo"})

    # TTL=0 表示关闭缓存：每次都真实打到上游，且不写入任何条目。
    assert len(upstream["calls"]) == 2
    assert main._cache_stats["hits"] == 0
    assert main._cache_stats["entries"] == 0


def test_cache_key_separates_query_params(monkeypatch: pytest.MonkeyPatch, _reset_cache: Any) -> None:
    upstream = _wire_fake_http(monkeypatch, {"full_name": "Guo-Yixin/test-coding-repo"})

    client.get("/api/repository", params={"owner": "Guo-Yixin", "repo": "test-coding-repo"})
    client.get("/api/repository", params={"owner": "Guo-Yixin", "repo": "other-repo"})
    client.get("/api/repository", params={"owner": "Guo-Yixin", "repo": "test-coding-repo"})

    # 两个不同仓库各打一次上游，第三次命中缓存，说明 key 未互相污染。
    assert len(upstream["calls"]) == 2
    assert main._cache_stats["hits"] == 1
    assert main._cache_stats["entries"] == 2


def test_health_reports_cache_observation(monkeypatch: pytest.MonkeyPatch, _reset_cache: Any) -> None:
    monkeypatch.setattr(main, "_github_request", _fake_github_response)

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

    first = client.get("/api/repository", params={"owner": "Guo-Yixin", "repo": "test-coding-repo"})
    second = client.get("/api/repository", params={"owner": "Guo-Yixin", "repo": "test-coding-repo"})

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


# --- /api/diagnostics -------------------------------------------------------


def _wire_probe_http(monkeypatch: pytest.MonkeyPatch, *, status_code: int = 200, raise_exc: Exception | None = None) -> dict[str, list[Any]]:
    """自检专用替身：可模拟成功、上游报错与网络异常三种形态。"""
    upstream: dict[str, list[Any]] = {"calls": []}

    class _FakeResponse:
        def __init__(self, url: str) -> None:
            self.url = url

        status_code = 200
        content = b"1"
        text = '{"message": "forbidden"}'

        def json(self) -> Any:
            if self.url.endswith("/rate_limit"):
                return {"core": {"limit": 5000, "remaining": 4999, "used": 1, "reset": 1790000000}}
            return {"full_name": "Guo-Yixin/test-coding-repo", "default_branch": "main", "private": False, "html_url": "https://github.com/Guo-Yixin/test-coding-repo"}

    class _FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def __enter__(self) -> "_FakeClient":
            return self

        def __exit__(self, *args: Any) -> bool:
            return False

        def request(self, method: str, url: str, params: Any = None, json: Any = None) -> Any:
            upstream["calls"].append((method, url))
            if raise_exc is not None:
                raise raise_exc
            response = _FakeResponse(url)
            response.status_code = status_code
            return response

    monkeypatch.setattr(main.httpx, "Client", _FakeClient)
    return upstream


def test_diagnostics_reports_rate_limit(monkeypatch: pytest.MonkeyPatch, _reset_cache: Any) -> None:
    upstream = _wire_probe_http(monkeypatch)

    response = client.get("/api/diagnostics")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["rate_limit"]["ok"] is True
    assert payload["rate_limit"]["remaining"] == 4999
    assert payload["rate_limit"]["limit"] == 5000
    assert payload["rate_limit"]["reset_in_seconds"] == 0
    # 未传 owner/repo 时跳过仓库探测，且不额外请求上游。
    assert payload["repository"]["skip"] is True
    assert payload["problems"] == []
    assert [url for _method, url in upstream["calls"]] == ["https://api.github.com/rate_limit"]


def test_diagnostics_probes_repository_when_owner_given(monkeypatch: pytest.MonkeyPatch, _reset_cache: Any) -> None:
    upstream = _wire_probe_http(monkeypatch)

    response = client.get("/api/diagnostics", params={"owner": "Guo-Yixin", "repo": "test-coding-repo"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["repository"]["ok"] is True
    assert payload["repository"].get("full_name") == "Guo-Yixin/test-coding-repo"
    assert payload["problems"] == []
    assert len(upstream["calls"]) == 2


def test_diagnostics_keeps_http_200_when_upstream_fails(monkeypatch: pytest.MonkeyPatch, _reset_cache: Any) -> None:
    """本接口语义是“报告状态”：上游 403 时外层仍是 200，失败写进子项。"""
    _wire_probe_http(monkeypatch, status_code=403)

    response = client.get("/api/diagnostics", params={"owner": "Guo-Yixin", "repo": "test-coding-repo"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["rate_limit"]["ok"] is False
    assert payload["rate_limit"]["status"] == 403
    assert payload["repository"]["ok"] is False
    assert payload["repository"]["status"] == 403
    # 与其它接口的“上游失败透传状态码”形成对照，因此这里必须仍是 200。
    assert payload["problems"] == ["rate_limit", "repository"]


def test_diagnostics_network_error_is_reported_not_raised(monkeypatch: pytest.MonkeyPatch, _reset_cache: Any) -> None:
    _wire_probe_http(monkeypatch, raise_exc=main.httpx.TimeoutException("timeout"))

    response = client.get("/api/diagnostics")

    assert response.status_code == 200
    payload = response.json()
    assert payload["rate_limit"]["ok"] is False
    # 网络异常（不是 HTTP 错误响应）时没有上游状态码，返回 status=None。
    assert payload["rate_limit"]["status"] is None
    assert "rate_limit" in payload["problems"]


def test_diagnostics_never_caches_and_hides_token(monkeypatch: pytest.MonkeyPatch, _reset_cache: Any) -> None:
    upstream = _wire_probe_http(monkeypatch)
    monkeypatch.setattr(main, "GITHUB_TOKEN", "ghp_secret_should_never_leak")

    first = client.get("/api/diagnostics")
    second = client.get("/api/diagnostics")

    assert first.status_code == second.status_code == 200
    # 自检本身就是探测意图，不写入也不读取缓存，两次都真实打到上游。
    assert main._cache_stats["entries"] == 0
    assert len(upstream["calls"]) == 2
    assert "ghp_secret_should_never_leak" not in first.text
    assert first.json()["rate_limit"]["credential"] == "token"


def test_diagnostics_accepts_partial_owner_without_probe(monkeypatch: pytest.MonkeyPatch, _reset_cache: Any) -> None:
    """只给 owner 不给 repo 时不应探测，也不应报错。"""
    upstream = _wire_probe_http(monkeypatch)

    response = client.get("/api/diagnostics", params={"owner": "Guo-Yixin"})

    assert response.status_code == 200
    assert response.json()["repository"]["skip"] is True
    assert len(upstream["calls"]) == 1
