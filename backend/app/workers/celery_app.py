from celery import Celery
from celery.exceptions import SoftTimeLimitExceeded

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "packaging_nesting",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

soft_time_limit = min(settings.task_soft_time_limit_sec, settings.task_hard_time_limit_sec)
celery_app.conf.update(
    task_track_started=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_time_limit=settings.task_hard_time_limit_sec,
    task_soft_time_limit=soft_time_limit,
    worker_prefetch_multiplier=max(1, settings.task_worker_prefetch_multiplier),
)
celery_app.conf.beat_schedule = {}


def build_maintenance_beat_schedule(config) -> dict:
    if not config.maintenance_scheduler_enabled:
        return {}
    return {
        "packaging-nesting-scheduled-maintenance": {
            "task": "packaging_nesting.enqueue_scheduled_maintenance",
            "schedule": max(60, config.maintenance_interval_minutes * 60),
        }
    }


celery_app.conf.beat_schedule = build_maintenance_beat_schedule(settings)


@celery_app.task(name="packaging_nesting.execute_work_task")
def execute_work_task_task(task_id: str) -> dict:
    from app.services.workflows import execute_work_task

    try:
        return execute_work_task(task_id).model_dump(mode="json")
    except SoftTimeLimitExceeded:
        from app.db.session import SessionLocal, init_db
        from app.services import repository

        init_db()
        with SessionLocal() as db:
            timed_out = repository.timeout_work_task(db, task_id, soft_time_limit, {"celery_soft_time_limit": True})
            if timed_out is None:
                raise
            return timed_out.model_dump(mode="json")


@celery_app.task(name="packaging_nesting.enqueue_scheduled_maintenance")
def enqueue_scheduled_maintenance_task() -> dict:
    from app.db.session import SessionLocal, init_db
    from app.services import repository
    from app.services.maintenance import build_default_maintenance_request
    from app.services.workflows import execute_work_task

    init_db()
    with SessionLocal() as db:
        request = build_default_maintenance_request(settings)
        task = repository.create_work_task(
            db,
            task_type="maintenance.run",
            target_type="maintenance",
            target_id="scheduled",
            actor_id="system",
            payload=request.model_dump(mode="json"),
            max_attempts=1,
            timeout_sec=600,
        )
    return execute_work_task(task.id).model_dump(mode="json")
