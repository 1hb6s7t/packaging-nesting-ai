from __future__ import annotations

from fastapi import BackgroundTasks

from app.core.config import get_settings
from app.services.workflows import execute_work_task


def dispatch_work_task(task_id: str, background_tasks: BackgroundTasks) -> None:
    if get_settings().task_execution_backend == "celery":
        from app.workers.celery_app import execute_work_task_task

        execute_work_task_task.delay(task_id)
        return
    background_tasks.add_task(execute_work_task, task_id)
