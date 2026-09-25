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
- `GET /api/diagnostics?owner=Guo-Yixin&repo=test-coding-repo`：上游自检。返回 `rate_limit`（GitHub 配额 limit/remaining/used 与 `reset_in_seconds`；该上游接口**不消耗配额**）、`cache`（缓存统计）与 `repository`（仅在同时传入 `owner` 和 `repo` 时探测仓库可达性，否则返回 `{"skip": true}`）。
- `GET /api/repository?owner=Guo-Yixin&repo=test-coding-repo`：读取 GitHub 仓库信息。
- `GET /api/pulls?owner=Guo-Yixin&repo=test-coding-repo&state=open`：列出 Pull Request（`state` 仅支持 `open`/`closed`/`all`，`per_page` 上限 100），仅返回白名单字段。**支持翻页**：可选 `page`（页号）或 `cursor`（等于上一页返回的 `next_page`），两者互斥。响应含 `per_page`、`page`、`has_more`、`next_page`。
- `GET /api/issues?owner=Guo-Yixin&repo=test-coding-repo&state=open`：列出 Issue，参数与裁剪规则同 `/api/pulls`（含翻页参数）；**带 `pull_request` 的条目会被过滤掉**，避免 PR 混入 Issue 列表。
- `GET /api/pulls/{number}/context`：读取 PR、普通评论、Review、Review Comment、Commit Status 和 Actions 状态。评论支持 `comments_page` / `comments_per_page`，响应含 `comments_has_more`、`comments_next_page`。
- `GET /api/issues/{number}/context?owner=Guo-Yixin&repo=test-coding-repo`：读取 Issue 上下文（标题、状态、作者、标签、创建时间）与评论列表，仅返回白名单字段。评论翻页参数同上。
- `POST /api/issues`：创建普通 Issue。
- `POST /api/pulls`：创建普通非 Draft PR，不执行 Merge。

### 示例

```powershell
# 就绪检查（无需 Token）
curl "http://127.0.0.1:8000/api/ready"
# -> {"ok": true, "status": "ready"}
# 上游自检：配额 + 仓库可达性（不含 owner/repo 时只查配额）
curl "http://127.0.0.1:8000/api/diagnostics?owner=Guo-Yixin&repo=test-coding-repo"
# -> {"ok": true, "token_configured": true,
#     "rate_limit": {"ok": true, "status": 200, "limit": 5000, "remaining": 4999, "reset_in_seconds": 1234, "credential": "token"},
#     "cache": {"ttl_seconds": 45, "enabled": true, ...},
#     "repository": {"ok": true, "status": 200, "full_name": "Guo-Yixin/test-coding-repo"},
#     "problems": []}
# 列出待处理的 Pull Request
curl "http://127.0.0.1:8000/api/pulls?owner=Guo-Yixin&repo=test-coding-repo&state=open"
# -> {"count": 1, "state": "open", "per_page": 30, "page": 1, "has_more": false, "next_page": null,
#     "pull_requests": [{"number": 3, "title": "...", "head": "feature", "base": "main", ...}]}
# 翻到第 2 页（也可以用 cursor=<上一页的 next_page>，两者不能同时传）
curl "http://127.0.0.1:8000/api/pulls?owner=Guo-Yixin&repo=test-coding-repo&state=open&page=2"
# -> {"count": 30, "state": "open", "per_page": 30, "page": 2, "has_more": true, "next_page": 3, "pull_requests": [...]}
# 列出 Issue（自动过滤 PR）
curl "http://127.0.0.1:8000/api/issues?owner=Guo-Yixin&repo=test-coding-repo&state=open"
# -> {"count": 2, "state": "open", "per_page": 30, "page": 1, "has_more": false, "next_page": null,
#     "issues": [{"number": 1, "title": "...", "labels": ["bug"], ...}]}
# 读取 Issue #1 的上下文
curl "http://127.0.0.1:8000/api/issues/1/context?owner=Guo-Yixin&repo=test-coding-repo"
# -> {"issue": {"number": 1, "title": "...", "state": "open", "user": "...", "labels": [], "comments": 0, "created_at": "..."},
#     "comments": [{"id": 1, "user": "...", "body": "...", "created_at": "...", "html_url": "..."}]}
```

说明：

- 列表接口每次消耗 1 次配额、上下文接口消耗 2 次以上配额，但相同请求在缓存有效期内只会打到 GitHub 一次；未配置 `GITHUB_TOKEN` 时限额较低，可能返回 403。
- **列表接口有意绕过缓存**：`has_more` 来自上游响应的 `Link` 头，而缓存层只保存响应体不保存响应头，因此两个列表接口不走缓存，每翻一页都真实消耗 1 次配额。
- `has_more` 与 `count` **不同源**，不要互相推断：`has_more` 以上游 `Link` 头为准；`count` 是本页**过滤后**的条数（`/api/issues` 会剔除 PR），因此可能出现「本页只有几条但仍有下一页」，也可能出现「本页刚好 30 条但已到最后一页」。
- 列表翻页可选 `page` 或 `cursor`（`cursor` 即上一页返回的 `next_page`），**两者互斥**，同时传返回 400；`page`/`cursor` 非正整数返回 400。
- `owner`/`repo` 格式不合法返回 400，`state` 不是 `open`/`closed`/`all` 返回 400，`per_page` 不在 1~100 之间返回 400，`comments_per_page` 不在 1~100 之间返回 400。
- 评论默认返回前 100 条，支持通过 `comments_page` / `comments_per_page` 翻页，并给出 `comments_has_more` / `comments_next_page`。
- 前端「读取 Pull Request 列表」「读取 Issue 列表」按钮会调用上面两个列表接口，点击列表条目即可把编号回填到输入框，再点「读取 Issue」查看上下文，从而补齐「仓库 → 列表 → 编号 → 上下文」的闭环。
- 前端列表支持翻页：PR 与 Issue **各自独立**维护页码与分页状态；「下一页」是否可用**只依据接口返回的 `has_more`**，绝不用「本页条数是否等于 `per_page`」推断。切换状态筛选或修改 Owner/Repository 会把页码重置为第 1 页并清空旧列表。
- 前端「自检」按钮调用 `/api/diagnostics`，把配额剩余与仓库可达性渲染到「接口返回」卡片和右上角状态徽章。

### 前端分页人工验收清单

仓库没有前端测试框架（无 `package.json`、无 Node 依赖），前端分页以人工冒烟为准。启动服务后逐条核对：

1. 第 1 页时「上一页」为禁用态（既有 `disabled` 属性，也有变灰样式）。
2. 翻到有下一页的列表，「下一页」可用；到达最后一页后自动变为禁用。
3. **关键防线**：选一个 PR/Issue 总数不足一页的仓库，此时「下一页」必须禁用——证明判定依据是 `has_more` 而不是条数。
4. PR 列表翻到第 2 页 → 切到 Issue 列表 → 再切回 PR 列表，两者页码互不干扰。
5. 快速连点「下一页」，不会发出重复请求，最终展示与最后一次点击一致。
6. 修改 Owner 或 Repository 输入框，页码立刻归 1 且旧列表被清空。
7. 把「状态筛选」切到 `closed`，页码归 1 并按 `state=closed` 重新请求。
8. 填入不存在的仓库或断网，列表显示「读取失败。」且页面不白屏。
9. 点击任一列表条目，「接口返回」卡片展示该条目且 Issue 编号被回填，再点「读取 Issue」能取回上下文。

## 自检接口的失败语义（与其它接口相反）

其余接口遵循「上游失败就透传上游状态码」的约定（例如上游 403 就返回 403）。`/api/diagnostics` 是**报告型接口**，语义刻意相反：

- **任何子项失败，外层 HTTP 仍然返回 200**，失败信息通过对应子项的 `ok: false`、`status` 与 `detail` 表达，例如 Token 无效或额度耗尽时：
  `{"rate_limit": {"ok": false, "status": 403, "detail": "GitHub API 请求失败: ..."}, "problems": ["rate_limit", "repository"]}`
- 判断自检是否通过，**必须看 `problems` 数组或各子项的 `ok`**，不能只看 HTTP 状态码。
- 网络异常（如超时）时没有上游状态码，子项 `status` 为 `null`，`detail` 中是脱敏后的异常类名。
- 子项之间互不影响：即使配额读取失败，仓库探测仍会照常执行并各自返回结果。
- **绝不返回 Token 本身**，只提供 `token_configured` 布尔值和表示凭据来源的 `credential` 字段。

### Token 状态区分

未配置 `GITHUB_TOKEN` 与 Token 无效都会表现为配额受限，二者可通过以下方式区分：

- `token_configured: false`：未配置 Token，配额按未认证（按 IP）计算。
- `token_configured: true` 但 `rate_limit.ok: false` / `status: 403`：Token 已配置但无效或已耗尽。

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
