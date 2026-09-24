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
- `GET /api/repository?owner=Guo-Yixin&repo=test-coding-repo`：读取 GitHub 仓库信息。
- `GET /api/pulls/{number}/context`：读取 PR、普通评论、Review、Review Comment、Commit Status 和 Actions 状态。
- `POST /api/issues`：创建普通 Issue。
- `POST /api/pulls`：创建普通非 Draft PR，不执行 Merge。

## 测试

```powershell
pytest -q
```
