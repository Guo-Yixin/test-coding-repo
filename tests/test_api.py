"""FastAPI Task API 接口测试，覆盖健康检查、任务列表与新增任务。"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health() -> None:
    """健康检查接口应返回 ok 状态与 200。"""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_list_tasks_returns_list() -> None:
    """任务列表接口应返回 200 与一个列表结构。"""
    response = client.get("/api/tasks")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_create_task() -> None:
    """新增任务应返回 201，并且任务出现在列表中。"""
    response = client.post("/api/tasks", json={"title": "买牛奶"})
    assert response.status_code == 201
    created = response.json()
    assert created["id"] >= 1
    assert created["title"] == "买牛奶"
    assert created["completed"] is False

    listing = client.get("/api/tasks").json()
    assert any(item["id"] == created["id"] for item in listing)


def test_create_task_completed_optional() -> None:
    """completed 字段可省略，默认应为 False。"""
    response = client.post("/api/tasks", json={"title": "写周报", "completed": True})
    assert response.status_code == 201
    created = response.json()
    assert created["completed"] is True


def test_get_task_by_id() -> None:
    """查询已存在的任务应返回 200，且字段与创建结果一致。"""
    created = client.post(
        "/api/tasks", json={"title": "看文档", "completed": True}
    ).json()

    response = client.get(f"/api/tasks/{created['id']}")
    assert response.status_code == 200
    assert response.json() == created


def test_get_task_not_found() -> None:
    """查询不存在的任务应返回 404，并给出统一的 detail 信息。"""
    response = client.get("/api/tasks/999999")
    assert response.status_code == 404
    assert response.json() == {"detail": "Task not found"}


def test_get_task_invalid_id_type() -> None:
    """id 不是整数时应由 FastAPI 参数校验拦下，返回 422。"""
    response = client.get("/api/tasks/abc")
    assert response.status_code == 422
