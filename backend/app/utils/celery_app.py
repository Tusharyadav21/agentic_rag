from celery import Celery
from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "artha_tasks",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "app.services.tasks.ingestion_tasks",
        "app.services.tasks.extraction_tasks",
    ]
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,
)
