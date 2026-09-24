"""FastAPI Task API 示例应用。

提供健康检查、任务列表、新增任务与单任务查询接口。

任务数据存放于进程内存，不引入数据库；服务重启后数据清空。
"""

from typing import List

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="FastAPI Task API")


class Task(BaseModel):
    """任务实体。"""

    id: int
    title: str
    completed: bool = False


class TaskCreate(BaseModel):
    """新增任务的请求体，仅 title 必填。"""

    title: str
    completed: bool = False


# 内存存储：保存任务的列表与自增 ID 计数器（删除不回收）。
_tasks: List[Task] = []
_next_id: int = 1


@app.get("/health")
def health() -> dict:
    """健康检查接口。"""
    return {"status": "ok"}


@app.get("/api/tasks", response_model=List[Task])
def list_tasks() -> List[Task]:
    """返回当前内存中的全部任务。"""
    return _tasks


@app.post("/api/tasks", response_model=Task, status_code=201)
def create_task(payload: TaskCreate) -> Task:
    """新增一个任务，返回带自增 id 的任务对象。"""
    global _next_id
    task = Task(id=_next_id, title=payload.title, completed=payload.completed)
    _next_id += 1
    _tasks.append(task)
    return task


@app.get("/api/tasks/{task_id}", response_model=Task)
def get_task(task_id: int) -> Task:
    """按 id 查询单个任务，未找到时返回 404。"""
    for task in _tasks:
        if task.id == task_id:
            return task
    raise HTTPException(status_code=404, detail="Task not found")
