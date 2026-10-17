"""Celery task modules.

``app/worker.py`` calls ``autodiscover_tasks(["app.tasks"])``, so any module
placed in this package is picked up automatically — but only if it is imported
here or named ``tasks.py``. Import new task modules below.
"""

from app.tasks import (
    crm,  # noqa: F401
    lock_sweep,  # noqa: F401
    notifications,  # noqa: F401
    ops,  # noqa: F401
    task_jobs,  # noqa: F401
)
