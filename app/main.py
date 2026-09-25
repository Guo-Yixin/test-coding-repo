from __future__ import annotations

import os
import re
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "static"
GITHUB_API_BASE_URL = os.getenv("GITHUB_API_BASE_URL", "https://api.github.com").rstrip("/")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
REPOSITORY_PART = re.compile(r"^[A-Za-z0-9_.-]+$")
ALLOWED_STATES = ("open", "closed", "all")
MAX_PER_PAGE = 100


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.getenv(name, "").strip()
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= minimum else default


# 进程内只读缓存：TTL=0 表示关闭缓存；条目上限按 LRU 淘汰，避免长期运行内存无界增长。
CACHE_TTL_SECONDS = _env_int("GITHUB_CACHE_TTL_SECONDS", 45)
CACHE_MAX_ENTRIES = _env_int("GITHUB_CACHE_MAX_ENTRIES", 128, minimum=1)
# 仅这些只读前缀参与缓存；POST 等写操作永不缓存。
CACHEABLE_PATH_PREFIXES = (
    "/repos/",
    "/rate_limit",
)
_cache: "OrderedDict[str, tuple[float, Any]]" = OrderedDict()
_cache_stats = {"hits": 0, "misses": 0, "entries": 0, "evictions": 0, "stores": 0}


class IssueRequest(BaseModel):
    owner: str
    repo: str
    title: str = Field(min_length=1, max_length=256)
    body: str = ""
    labels: list[str] = Field(default_factory=list)


class PullRequestRequest(BaseModel):
    owner: str
    repo: str
    head: str = Field(min_length=1, max_length=255)
    base: str | None = None
    title: str = Field(min_length=1, max_length=256)
    body: str = ""


app = FastAPI(title="Coding Agent Minimal GitHub API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _validate_repository(owner: str, repo: str) -> tuple[str, str]:
    owner = owner.strip()
    repo = repo.strip().removesuffix(".git")
    if not owner or not repo or not REPOSITORY_PART.fullmatch(owner) or not REPOSITORY_PART.fullmatch(repo):
        raise HTTPException(status_code=400, detail="owner/repo 格式不正确")
    return owner, repo


def _cache_key(method: str, path: str, params: dict[str, Any] | None) -> str:
    normalized = "&".join(f"{key}={params[key]}" for key in sorted(params or {}))
    return f"{method} {path}?{normalized}"


def _is_cacheable(method: str, path: str) -> bool:
    """只有 GET 且命中白名单前缀的请求才允许进入缓存，写操作永不缓存。"""
    return method.upper() == "GET" and CACHE_TTL_SECONDS > 0 and path.startswith(CACHEABLE_PATH_PREFIXES)


def _cache_lookup(key: str) -> tuple[bool, Any]:
    entry = _cache.get(key)
    if entry is None:
        _cache_stats["misses"] += 1
        return False, None
    created_at, value = entry
    if time.monotonic() - created_at >= CACHE_TTL_SECONDS:
        _cache.pop(key, None)
        _cache_stats["misses"] += 1
        _cache_stats["entries"] = len(_cache)
        return False, None
    _cache.move_to_end(key)
    _cache_stats["hits"] += 1
    return True, value


def _cache_store(key: str, value: Any) -> None:
    _cache[key] = (time.monotonic(), value)
    _cache.move_to_end(key)
    _cache_stats["stores"] += 1
    while len(_cache) > CACHE_MAX_ENTRIES:
        _cache.popitem(last=False)
        _cache_stats["evictions"] += 1
    _cache_stats["entries"] = len(_cache)


def _cache_clear() -> None:
    _cache.clear()
    _cache_stats["entries"] = 0


def _github_request(method: str, path: str, *, params: dict[str, Any] | None = None, json: dict[str, Any] | None = None) -> Any:
    cacheable = _is_cacheable(method, path)
    cache_key = _cache_key(method, path, params) if cacheable else ""
    if cacheable:
        hit, cached_value = _cache_lookup(cache_key)
        if hit:
            return cached_value
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": os.getenv("GITHUB_API_VERSION", "2022-11-28"),
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    try:
        with httpx.Client(timeout=20, headers=headers) as client:
            response = client.request(method, f"{GITHUB_API_BASE_URL}{path}", params=params, json=json)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"GitHub API 网络请求失败: {exc.__class__.__name__}") from exc
    if response.status_code >= 400:
        detail = response.text[:500].replace(GITHUB_TOKEN, "***") if GITHUB_TOKEN else response.text[:500]
        raise HTTPException(status_code=response.status_code, detail=f"GitHub API 请求失败: {detail}")
    data = response.json() if response.content else {}
    if cacheable:
        # 仅缓存成功结果；失败已在上面透传，不会被冻结。
        _cache_store(cache_key, data)
    return data


def _redact(value: str) -> str:
    """从任意文本中抹掉 Token，避免诊断信息意外回显凭据。"""
    text = str(value)[:500]
    if GITHUB_TOKEN:
        text = text.replace(GITHUB_TOKEN, "***")
    return text


def _github_request_with_headers(
    method: str, path: str, *, params: dict[str, Any] | None = None
) -> tuple[Any, str | None]:
    """平行于 `_github_request` 的只读上游请求，额外返回 `Link` 头。

    与 `_github_request` 的差异只有两点：

    1. 额外返回响应头里的 `Link`，用于判断「还有下一页」；
    2. **不读写缓存**。`Link` 属于响应头，缓存层目前只保存 body，
       若复用缓存就会丢失分页信号，因此这里刻意绕过缓存。

    其余约定与 `_github_request` 保持一致：同样的请求头、同样的超时与
    错误透传（上游失败 → 相同状态码的 `HTTPException`），脱敏统一走 `_redact`。
    """
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": os.getenv("GITHUB_API_VERSION", "2022-11-28"),
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    try:
        with httpx.Client(timeout=20, headers=headers) as client:
            response = client.request(method, f"{GITHUB_API_BASE_URL}{path}", params=params)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"GitHub API 网络请求失败: {exc.__class__.__name__}") from exc
    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=f"GitHub API 请求失败: {_redact(response.text)}",
        )
    data = response.json() if response.content else {}
    link_header = response.headers.get("Link")
    return data, link_header


def _parse_link_header(link: str | None) -> tuple[bool, int | None]:
    """解析 GitHub 的 `Link` 响应头，返回 `(has_more, next_page)`。

    只关心 `rel="next"` 这一个准确的分页信号：它由 GitHub 自己给出，
    因此能区分「本页刚好装满但确实没有下一页」与「还有下一页」这两种场景，
    这正是「靠条数猜」做不到的。

    任何畸形输入都退化为 `(False, None)`，绝不抛异常：判断失误最多让调用方
    看不到下一页提示，而不应该让接口直接 500。
    """
    if not link:
        return False, None
    for segment in str(link).split(","):
        if 'rel="next"' not in segment:
            continue
        match = re.search(r"<([^>]*)>", segment)
        if not match:
            continue
        page_match = re.search(r"[?&]page=(\d+)", match.group(1))
        if not page_match:
            # GitHub 的分页 next URL 应包含可解析的 page；畸形链接不应误报还有下一页。
            continue
        next_page = int(page_match.group(1))
        if next_page < 1:
            continue
        return True, next_page
    return False, None


def _probe_github(path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """探测型上游请求：与 `_github_request` 平行，但**不外抛异常**、**不读写缓存**。

    `/api/diagnostics` 的语义是「报告状态」而不是「执行操作」，因此失败也必须
    以响应体子项的形式返回，外层 HTTP 保持 200。
    """
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": os.getenv("GITHUB_API_VERSION", "2022-11-28"),
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    try:
        with httpx.Client(timeout=20, headers=headers) as client:
            response = client.request("GET", f"{GITHUB_API_BASE_URL}{path}", params=params)
    except httpx.HTTPError as exc:
        return {"ok": False, "status": None, "detail": f"网络请求失败: {exc.__class__.__name__}"}
    if response.status_code >= 400:
        return {
            "ok": False,
            "status": response.status_code,
            "detail": f"GitHub API 请求失败: {_redact(response.text)}",
        }
    try:
        data = response.json() if response.content else {}
    except ValueError:
        return {"ok": False, "status": response.status_code, "detail": "GitHub API 返回了非 JSON 内容"}
    return {"ok": True, "status": response.status_code, "data": data}


def _rate_limit_summary() -> dict[str, Any]:
    """读取 `/rate_limit`（该接口不消耗配额），并换算重置时间。"""
    probe = _probe_github("/rate_limit")
    if not probe["ok"]:
        return {"ok": False, "status": probe["status"], "detail": probe["detail"]}
    core = (probe["data"] or {}).get("core") or {}
    reset_at = core.get("reset")
    remaining_seconds: int | None = None
    reset_utc: str | None = None
    if isinstance(reset_at, (int, float)):
        remaining_seconds = max(0, int(reset_at - time.time()))
        reset_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(reset_at))
    return {
        "ok": True,
        "status": probe["status"],
        "limit": core.get("limit"),
        "remaining": core.get("remaining"),
        "used": core.get("used"),
        "reset_utc": reset_utc,
        "reset_in_seconds": remaining_seconds,
        # 未认证时额度按来源 IP 计，与 Token 额度不同源，这里显式区分。
        "credential": "token" if GITHUB_TOKEN else "anonymous",
        "token_configured": bool(GITHUB_TOKEN),
    }


def _repository_probe(owner: str, repo: str) -> dict[str, Any]:
    owner, repo = _validate_repository(owner, repo)
    probe = _probe_github(f"/repos/{owner}/{repo}")
    if not probe["ok"]:
        return {"ok": False, "status": probe["status"], "detail": probe["detail"], "full_name": f"{owner}/{repo}"}
    data = probe["data"] or {}
    return {
        "ok": True,
        "status": probe["status"],
        "full_name": data.get("full_name"),
        "default_branch": data.get("default_branch"),
        "private": data.get("private"),
        "html_url": data.get("html_url"),
    }


def _cache_snapshot() -> dict[str, Any]:
    return {
        "ttl_seconds": CACHE_TTL_SECONDS,
        "enabled": CACHE_TTL_SECONDS > 0,
        "max_entries": CACHE_MAX_ENTRIES,
        "hits": _cache_stats["hits"],
        "misses": _cache_stats["misses"],
        "entries": _cache_stats["entries"],
        "evictions": _cache_stats["evictions"],
        "stores": _cache_stats["stores"],
    }


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "test-coding-repo",
        "github_token_configured": bool(GITHUB_TOKEN),
        "github_api_base_url": GITHUB_API_BASE_URL,
        "cache": {
            "ttl_seconds": CACHE_TTL_SECONDS,
            "max_entries": CACHE_MAX_ENTRIES,
            "enabled": CACHE_TTL_SECONDS > 0,
            "hits": _cache_stats["hits"],
            "misses": _cache_stats["misses"],
            "entries": _cache_stats["entries"],
            "evictions": _cache_stats["evictions"],
            "stores": _cache_stats["stores"],
        },
    }


@app.get("/api/ready")
def ready() -> dict[str, Any]:
    """进程内就绪检查：不访问 GitHub，不读取 Token。"""
    return {"ok": True, "status": "ready"}


@app.get("/api/diagnostics")
def diagnostics(owner: str | None = None, repo: str | None = None) -> dict[str, Any]:
    """上游连通性与配额自检。

    与其它接口不同：本接口是「报告状态」而非「执行操作」，因此**无论子项成功与否，
    外层 HTTP 都返回 200**，失败信息放在对应子项的 `ok`/`status`/`detail` 里。
    """
    result: dict[str, Any] = {
        "ok": True,
        "github_api_base_url": GITHUB_API_BASE_URL,
        "token_configured": bool(GITHUB_TOKEN),
        "rate_limit": _rate_limit_summary(),
        "cache": _cache_snapshot(),
    }
    if owner and repo:
        result["repository"] = _repository_probe(owner, repo)
    else:
        result["repository"] = {"skip": True, "reason": "未提供 owner/repo，跳过仓库可达性探测"}
    # 顶层 ok 只反映「自检是否跑完」，具体失败看各子项的 ok。
    result["problems"] = [
        name
        for name in ("rate_limit", "repository")
        if result.get(name) and result[name].get("ok") is False
    ]
    return result


@app.get("/api/repository")
def repository(owner: str = Query(...), repo: str = Query(...)) -> dict[str, Any]:
    owner, repo = _validate_repository(owner, repo)
    data = _github_request("GET", f"/repos/{owner}/{repo}")
    return {
        "provider": "github",
        "full_name": data.get("full_name"),
        "name": data.get("name"),
        "owner": (data.get("owner") or {}).get("login", owner),
        "default_branch": data.get("default_branch"),
        "private": data.get("private"),
        "description": data.get("description"),
        "html_url": data.get("html_url"),
        "open_issues_count": data.get("open_issues_count"),
    }


def _validate_state(state: str) -> str:
    state = state.strip().lower()
    if state not in ALLOWED_STATES:
        raise HTTPException(status_code=400, detail=f"state 只能是 {'/'.join(ALLOWED_STATES)}")
    return state


def _validate_per_page(per_page: int) -> int:
    if per_page < 1 or per_page > MAX_PER_PAGE:
        raise HTTPException(status_code=400, detail=f"per_page 必须在 1 到 {MAX_PER_PAGE} 之间")
    return per_page


def _validate_comments_paging(comments_page: int, comments_per_page: int) -> tuple[int, int]:
    """评论翻页参数校验；默认值 1 / 100 与改动前的行为保持一致。"""
    if comments_per_page < 1 or comments_per_page > MAX_PER_PAGE:
        raise HTTPException(status_code=400, detail=f"comments_per_page 必须在 1 到 {MAX_PER_PAGE} 之间")
    if comments_page < 1:
        raise HTTPException(status_code=400, detail="comments_page 必须是正整数")
    return comments_page, comments_per_page


def _resolve_page(page: int | None, cursor: int | None) -> int | None:
    """把 `page` / `cursor` 归一化为要透传给上游的页号。

    `cursor` 就是上一页响应里的 `next_page`，因此两者语义等价、不能同时使用。
    `page` 刻意不设自身上限，越界由 GitHub 判定，避免与上游规则重复维护。
    """
    if cursor is not None:
        if page is not None:
            raise HTTPException(status_code=400, detail="cursor 与 page 不能同时使用")
        if cursor < 1:
            raise HTTPException(status_code=400, detail="cursor 必须是正整数")
        return cursor
    if page is not None and page < 1:
        raise HTTPException(status_code=400, detail="page 必须是正整数")
    return page


def _user_login(payload: Any) -> str | None:
    return (payload.get("user") or {}).get("login") if isinstance(payload, dict) else None


def _trim_pull_request(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "number": item.get("number"),
        "title": item.get("title"),
        "state": item.get("state"),
        "draft": bool(item.get("draft")),
        "user": _user_login(item),
        "head": (item.get("head") or {}).get("ref"),
        "base": (item.get("base") or {}).get("ref"),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "html_url": item.get("html_url"),
    }


def _trim_issue(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "number": item.get("number"),
        "title": item.get("title"),
        "state": item.get("state"),
        "user": _user_login(item),
        "labels": [label.get("name") for label in (item.get("labels") or []) if isinstance(label, dict)],
        "comments": item.get("comments"),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "html_url": item.get("html_url"),
    }


@app.get("/api/pulls")
def list_pull_requests(
    owner: str = Query(...),
    repo: str = Query(...),
    state: str = Query("open"),
    per_page: int = Query(30),
    page: int | None = Query(None),
    cursor: int | None = Query(None),
) -> dict[str, Any]:
    """按状态列出仓库 Pull Request，只返回白名单字段。

    `has_more`/`next_page` 来自上游 `Link` 头，而不是「本页条数是否等于 per_page」，
    因此「刚好装满但确实没有下一页」不会被误报为还有下一页。
    """
    owner, repo = _validate_repository(owner, repo)
    state = _validate_state(state)
    per_page = _validate_per_page(per_page)
    resolved_page = _resolve_page(page, cursor)
    params: dict[str, Any] = {"state": state, "per_page": per_page, "sort": "updated", "direction": "desc"}
    if resolved_page is not None:
        # 只在显式翻页时才带上 page，避免给首屏请求平添参数。
        params["page"] = resolved_page
    items, link_header = _github_request_with_headers("GET", f"/repos/{owner}/{repo}/pulls", params=params)
    pulls = [_trim_pull_request(item) for item in (items or []) if isinstance(item, dict)]
    has_more, next_page = _parse_link_header(link_header)
    return {
        "count": len(pulls),
        "state": state,
        "per_page": per_page,
        "page": resolved_page if resolved_page is not None else 1,
        "has_more": has_more,
        "next_page": next_page,
        "pull_requests": pulls,
    }


@app.get("/api/issues")
def list_issues(
    owner: str = Query(...),
    repo: str = Query(...),
    state: str = Query("open"),
    per_page: int = Query(30),
    page: int | None = Query(None),
    cursor: int | None = Query(None),
) -> dict[str, Any]:
    """按状态列出仓库 Issue；GitHub 的 issues 接口会混入 PR，这里显式过滤。

    注意 `count` 是**本页过滤后**的条数，而 `has_more` 以上游 `Link` 为准，
    两者不同源：即使本页条数很少，也可能仍然存在下一页。
    """
    owner, repo = _validate_repository(owner, repo)
    state = _validate_state(state)
    per_page = _validate_per_page(per_page)
    resolved_page = _resolve_page(page, cursor)
    params: dict[str, Any] = {"state": state, "per_page": per_page, "sort": "updated", "direction": "desc"}
    if resolved_page is not None:
        params["page"] = resolved_page
    items, link_header = _github_request_with_headers("GET", f"/repos/{owner}/{repo}/issues", params=params)
    issues = [
        _trim_issue(item)
        for item in (items or [])
        if isinstance(item, dict) and "pull_request" not in item
    ]
    has_more, next_page = _parse_link_header(link_header)
    return {
        "count": len(issues),
        "state": state,
        "per_page": per_page,
        "page": resolved_page if resolved_page is not None else 1,
        "has_more": has_more,
        "next_page": next_page,
        "issues": issues,
    }


@app.get("/api/pulls/{number}/context")
def pull_request_context(
    number: int,
    owner: str = Query(...),
    repo: str = Query(...),
    comments_page: int = Query(1),
    comments_per_page: int = Query(100),
) -> dict[str, Any]:
    owner, repo = _validate_repository(owner, repo)
    comments_page, comments_per_page = _validate_comments_paging(comments_page, comments_per_page)
    pull_request = _github_request("GET", f"/repos/{owner}/{repo}/pulls/{number}")
    head_sha = ((pull_request.get("head") or {}).get("sha") or "") if isinstance(pull_request, dict) else ""
    comments, comments_link = _github_request_with_headers(
        "GET",
        f"/repos/{owner}/{repo}/issues/{number}/comments",
        params={"page": comments_page, "per_page": comments_per_page},
    )
    review_comments = _github_request("GET", f"/repos/{owner}/{repo}/pulls/{number}/comments", params={"per_page": 100})
    reviews = _github_request("GET", f"/repos/{owner}/{repo}/pulls/{number}/reviews", params={"per_page": 100})
    statuses = _github_request("GET", f"/repos/{owner}/{repo}/commits/{head_sha}/statuses", params={"per_page": 100}) if head_sha else []
    runs_data = _github_request("GET", f"/repos/{owner}/{repo}/actions/runs", params={"head_sha": head_sha, "per_page": 50}) if head_sha else {}
    comments_has_more, comments_next_page = _parse_link_header(comments_link)
    return {
        "pull_request": pull_request,
        "comments_page": comments_page,
        "comments_per_page": comments_per_page,
        "comments_has_more": comments_has_more,
        "comments_next_page": comments_next_page,
        "comments": comments,
        "review_comments": review_comments,
        "reviews": reviews,
        "commit_statuses": statuses,
        "workflow_runs": (runs_data or {}).get("workflow_runs", []) if isinstance(runs_data, dict) else [],
    }


@app.get("/api/issues/{number}/context")
def issue_context(
    number: int,
    owner: str = Query(...),
    repo: str = Query(...),
    comments_page: int = Query(1),
    comments_per_page: int = Query(100),
) -> dict[str, Any]:
    owner, repo = _validate_repository(owner, repo)
    comments_page, comments_per_page = _validate_comments_paging(comments_page, comments_per_page)
    issue = _github_request("GET", f"/repos/{owner}/{repo}/issues/{number}")
    comments, comments_link = _github_request_with_headers(
        "GET",
        f"/repos/{owner}/{repo}/issues/{number}/comments",
        params={"page": comments_page, "per_page": comments_per_page},
    )
    comments_has_more, comments_next_page = _parse_link_header(comments_link)
    return {
        "comments_page": comments_page,
        "comments_per_page": comments_per_page,
        "comments_has_more": comments_has_more,
        "comments_next_page": comments_next_page,
        "issue": {
            "number": issue.get("number"),
            "title": issue.get("title"),
            "state": issue.get("state"),
            "html_url": issue.get("html_url"),
            "user": (issue.get("user") or {}).get("login"),
            "labels": [label.get("name") for label in (issue.get("labels") or []) if isinstance(label, dict)],
            "comments": issue.get("comments"),
            "created_at": issue.get("created_at"),
        },
        "comments": [
            {
                "id": comment.get("id"),
                "user": (comment.get("user") or {}).get("login"),
                "body": comment.get("body"),
                "created_at": comment.get("created_at"),
                "html_url": comment.get("html_url"),
            }
            for comment in (comments or [])
            if isinstance(comment, dict)
        ],
    }


@app.post("/api/issues")
def create_issue(payload: IssueRequest) -> dict[str, Any]:
    owner, repo = _validate_repository(payload.owner, payload.repo)
    data = _github_request(
        "POST",
        f"/repos/{owner}/{repo}/issues",
        json={"title": payload.title, "body": payload.body, "labels": payload.labels},
    )
    return {"url": data.get("html_url"), "number": data.get("number"), "title": data.get("title")}


@app.post("/api/pulls")
def create_pull_request(payload: PullRequestRequest) -> dict[str, Any]:
    owner, repo = _validate_repository(payload.owner, payload.repo)
    base = payload.base
    if not base:
        repository_data = _github_request("GET", f"/repos/{owner}/{repo}")
        base = repository_data.get("default_branch") or "main"
    data = _github_request(
        "POST",
        f"/repos/{owner}/{repo}/pulls",
        json={"title": payload.title, "body": payload.body, "head": payload.head, "base": base, "draft": False},
    )
    return {"url": data.get("html_url"), "number": data.get("number"), "state": data.get("state"), "draft": data.get("draft", False)}
