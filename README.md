# test-coding-repo

这是 CODING Agent 的 GitHub.com 端到端验收仓库，包含一个最小 FastAPI 服务和浏览器界面。

## 启动

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

复制 `.env.example` 为 `.env`，在本地填写 `GITHUB_TOKEN`。不要提交真实 Token。

打开 <http://127.0.0.1:8000/>，点击“检查服务”和“读取 GitHub 仓库”。默认验收仓库为 `Guo-Yixin/test-coding-repo`。

## 接口

- `GET /api/health`：检查服务和 GitHub Token 配置状态；额外返回只读缓存的配置与命中统计（`cache` 字段）。
- `GET /api/ready`：就绪检查，服务可接收流量时返回 `{"ok": true, "status": "ready"}`；不访问 GitHub，不需要 `GITHUB_TOKEN`。
- `GET /api/repository?owner=Guo-Yixin&repo=test-coding-repo`：读取 GitHub 仓库信息。
- `GET /api/pulls?owner=Guo-Yixin&repo=test-coding-repo&state=open`：列出 Pull Request（`state` 仅支持 `open`/`closed`/`all`，`per_page` 上限 100），仅返回白名单字段。
- `GET /api/issues?owner=Guo-Yixin&repo=test-coding-repo&state=open`：列出 Issue，参数与裁剪规则同 `/api/pulls`；**带 `pull_request` 的条目会被过滤掉**，避免 PR 混入 Issue 列表。
- `GET /api/pulls/{number}/context`：读取 PR、普通评论、Review、Review Comment、Commit Status 和 Actions 状态。
- `GET /api/issues/{number}/context?owner=Guo-Yixin&repo=test-coding-repo`：读取 Issue 上下文（标题、状态、作者、标签、创建时间）与评论列表，仅返回白名单字段。
- `POST /api/issues`：创建普通 Issue。
- `POST /api/pulls`：创建普通非 Draft PR，不执行 Merge。

### 示例

```powershell
# 就绪检查（无需 Token）
curl "http://127.0.0.1:8000/api/ready"
# -> {"ok": true, "status": "ready"}
# 列出待处理的 Pull Request
curl "http://127.0.0.1:8000/api/pulls?owner=Guo-Yixin&repo=test-coding-repo&state=open"
# -> {"count": 1, "state": "open", "pull_requests": [{"number": 3, "title": "...", "head": "feature", "base": "main", ...}]}
# 列出 Issue（自动过滤 PR）
curl "http://127.0.0.1:8000/api/issues?owner=Guo-Yixin&repo=test-coding-repo&state=open"
# -> {"count": 2, "state": "open", "issues": [{"number": 1, "title": "...", "labels": ["bug"], ...}]}
# 读取 Issue #1 的上下文
curl "http://127.0.0.1:8000/api/issues/1/context?owner=Guo-Yixin&repo=test-coding-repo"
# -> {"issue": {"number": 1, "title": "...", "state": "open", "user": "...", "labels": [], "comments": 0, "created_at": "..."},
#     "comments": [{"id": 1, "user": "...", "body": "...", "created_at": "...", "html_url": "..."}]}
```

说明：

- 列表接口每次消耗 1 次配额、上下文接口消耗 2 次以上配额，但相同请求在缓存有效期内只会打到 GitHub 一次；未配置 `GITHUB_TOKEN` 时限额较低，可能返回 403。
- 评论最多返回前 100 条，暂不支持分页。
- `owner`/`repo` 格式不合法返回 400，`state` 不是 `open`/`closed`/`all` 返回 400，`per_page` 不在 1~100 之间返回 400。
- 前端「读取 Pull Request 列表」「读取 Issue 列表」按钮会调用上面两个列表接口，点击列表条目即可把编号回填到输入框，再点「读取 Issue」查看上下文，从而补齐「仓库 → 列表 → 编号 → 上下文」的闭环。

## 只读缓存

所有 `GET` 且命中白名单前缀（`/repos/`、`/rate_limit`）的上游请求都会经过进程内缓存，写操作（`POST /api/issues`、`POST /api/pulls`）**永不缓存**：

- 缓存键是 `method + path + 排序后的查询参数`，因此不同 `state`/`per_page` 的列表请求不会互相污染。
- 只有成功（HTTP < 400）的响应才会写入缓存；上游报错直接透传，不会被缓存“冻住”，避免限流期间一直拿到旧错误。
- 命中缓存的请求不再消耗 GitHub API 配额：同一个 Issue 上下文连读两次，第二次上游调用数为 0。
- 条目上限达到后会按 LRU 淘汰最久未使用的条目，防止长期运行内存无界增长。
- 配置项：`GITHUB_CACHE_TTL_SECONDS`（默认 45，设为 `0` 表示完全关闭缓存）、`GITHUB_CACHE_MAX_ENTRIES`（默认 128）。
- 通过 `GET /api/health` 的 `cache` 字段可查看 `ttl_seconds`、`enabled`、`hits`、`misses`、`entries`、`evictions`、`stores`。
- 已知取舍：缓存位于进程内存，多个 worker（`uvicorn --workers N`）之间不共享；缓存生效期间可能短暂读到旧数据，可用较短的 TTL 或把 TTL 设为 `0` 规避。

### 示例

```powershell
# 关闭缓存后运行（便于排查“刚提交却没看到”的问题）
$env:GITHUB_CACHE_TTL_SECONDS = "0"
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
# 查看缓存统计
curl "http://127.0.0.1:8000/api/health"
# -> {"ok": true, ..., "cache": {"ttl_seconds": 45, "enabled": true, "hits": 1, "misses": 1, ...}}
```

## 测试

```powershell
pip install -r requirements.txt
pytest -q
```

测试通过测试替身拦截 GitHub 调用，不会发出真实网络请求。
