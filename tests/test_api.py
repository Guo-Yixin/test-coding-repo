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
