"""Celery task modules.

``app/worker.py`` calls ``autodiscover_tasks(["app.tasks"])``, so any module
placed in this package is picked up automatically — but only if it is imported
here or named ``tasks.py``. Import new task modules below.
"""

from app.tasks import (
    lock_sweep,  # noqa: F401
    notifications,  # noqa: F401
)
