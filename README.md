# FastAPI Task API

一个最小但完整的 FastAPI 示例项目，提供任务（Task）的增查接口。
任务数据保存在进程内存中，不引入数据库，服务重启后数据会清空。

## 安装

```bash
pip install -e ".[test]"
```

## 启动

```bash
uvicorn app.main:app --reload
```

启动后访问：

- 接口文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/health>

## 接口

| 方法   | 路径                     | 请求体                                | 说明                     |
|--------|--------------------------|---------------------------------------|--------------------------|
| GET    | `/health`                | 无                                    | 健康检查                 |
| GET    | `/api/tasks`             | 无                                    | 返回任务列表             |
| POST   | `/api/tasks`             | `{"title": str, "completed"?: bool}`  | 新增任务，成功返回 201   |
| GET    | `/api/tasks/{task_id}`   | 无                                    | 按 id 查询任务，未找到返回 404 |

### 示例

```bash
# 健康检查
curl http://127.0.0.1:8000/health
# -> {"status": "ok"}

# 列出任务
curl http://127.0.0.1:8000/api/tasks
# -> []

# 新增任务
curl -X POST http://127.0.0.1:8000/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"title": "买牛奶"}'
# -> {"id": 1, "title": "买牛奶", "completed": false}

# 查询单个任务
curl http://127.0.0.1:8000/api/tasks/1
# -> {"id": 1, "title": "买牛奶", "completed": false}

# 查询不存在的任务
curl -i http://127.0.0.1:8000/api/tasks/999
# -> HTTP/1.1 404 Not Found
# -> {"detail": "Task not found"}
```

## 测试

```bash
pytest
# 或
python -m pytest tests/
```
