from __future__ import annotations

import os
import re
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


def _github_request(method: str, path: str, *, params: dict[str, Any] | None = None, json: dict[str, Any] | None = None) -> Any:
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
    return response.json() if response.content else {}


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
    }


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


@app.get("/api/pulls/{number}/context")
def pull_request_context(number: int, owner: str = Query(...), repo: str = Query(...)) -> dict[str, Any]:
    owner, repo = _validate_repository(owner, repo)
    pull_request = _github_request("GET", f"/repos/{owner}/{repo}/pulls/{number}")
    head_sha = ((pull_request.get("head") or {}).get("sha") or "") if isinstance(pull_request, dict) else ""
    comments = _github_request("GET", f"/repos/{owner}/{repo}/issues/{number}/comments", params={"per_page": 100})
    review_comments = _github_request("GET", f"/repos/{owner}/{repo}/pulls/{number}/comments", params={"per_page": 100})
    reviews = _github_request("GET", f"/repos/{owner}/{repo}/pulls/{number}/reviews", params={"per_page": 100})
    statuses = _github_request("GET", f"/repos/{owner}/{repo}/commits/{head_sha}/statuses", params={"per_page": 100}) if head_sha else []
    runs_data = _github_request("GET", f"/repos/{owner}/{repo}/actions/runs", params={"head_sha": head_sha, "per_page": 50}) if head_sha else {}
    return {
        "pull_request": pull_request,
        "comments": comments,
        "review_comments": review_comments,
        "reviews": reviews,
        "commit_statuses": statuses,
        "workflow_runs": (runs_data or {}).get("workflow_runs", []) if isinstance(runs_data, dict) else [],
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
