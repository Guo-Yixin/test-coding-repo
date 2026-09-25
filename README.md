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

- `GET /api/health`：检查服务和 GitHub Token 配置状态。
- `GET /api/ready`：就绪检查，服务可接收流量时返回 `{"ok": true, "status": "ready"}`；不访问 GitHub，不需要 `GITHUB_TOKEN`。
- `GET /api/repository?owner=Guo-Yixin&repo=test-coding-repo`：读取 GitHub 仓库信息。
- `GET /api/pulls/{number}/context`：读取 PR、普通评论、Review、Review Comment、Commit Status 和 Actions 状态。
- `GET /api/issues/{number}/context?owner=Guo-Yixin&repo=test-coding-repo`：读取 Issue 上下文（标题、状态、作者、标签、创建时间）与评论列表，仅返回白名单字段。
- `POST /api/issues`：创建普通 Issue。
- `POST /api/pulls`：创建普通非 Draft PR，不执行 Merge。

### 示例

```powershell
# 就绪检查（无需 Token）
curl "http://127.0.0.1:8000/api/ready"
# -> {"ok": true, "status": "ready"}
# 读取 Issue #1 的上下文
curl "http://127.0.0.1:8000/api/issues/1/context?owner=Guo-Yixin&repo=test-coding-repo"
# -> {"issue": {"number": 1, "title": "...", "state": "open", "user": "...", "labels": [], "comments": 0, "created_at": "..."},
#     "comments": [{"id": 1, "user": "...", "body": "...", "created_at": "...", "html_url": "..."}]}
```

说明：

- 每次调用会消耗 2 次 GitHub API 配额；未配置 `GITHUB_TOKEN` 时限额较低，可能返回 403。
- 评论最多返回前 100 条，暂不支持分页。

## 测试

```powershell
pip install -r requirements.txt
pytest -q
```

测试通过测试替身拦截 GitHub 调用，不会发出真实网络请求。
