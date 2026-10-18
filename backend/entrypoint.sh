#!/usr/bin/env sh
#
# Container entrypoint. One image, five process types.
#
# The API, the Celery worker and the Celery beat scheduler all run the same
# code and the same dependencies; only the command differs. Building three
# images to vary one line would mean three things to keep in step.
#
# Which one this container becomes is chosen by PROCESS_TYPE, so a deployment
# is described entirely by environment variables that live in the repository's
# runbook. NFR-08: no manual configuration steps that live only on someone's
# machine — in particular, nobody has to remember to type a start command into
# a dashboard, and forgetting to do so cannot silently produce a second API
# where a beat scheduler was intended.
#
#   api     (default) the FastAPI application
#   worker             Celery worker — notifications (FR-NOTIF)
#   beat               Celery beat — the nightly lock sweep (spec Q-04)
#   worker_crm_sync    Celery worker for the CRM's Gmail sync only (spec 007
#                      FR-CRM-SYNC-04): the crm_sync queue, one task at a time.
#                      The one service given GOOGLE_SERVICE_ACCOUNT_JSON; started
#                      only once sync is switched on.
#   worker_crm_ai      Celery worker for the CRM's AI jobs only (spec 009
#                      FR-CRMAI-JOB-04): the crm-ai queue, one task at a time.
#                      The one service given ANTHROPIC_API_KEY and
#                      FIREFLIES_API_KEY.
#
# An unknown value fails loudly rather than defaulting to the API. A typo like
# PROCESS_TYPE=beet must not produce a healthy-looking service that quietly
# never runs the sweep.

set -e

case "${PROCESS_TYPE:-api}" in
  api)
    # start_api.py reads $PORT, which the platform assigns.
    exec python start_api.py
    ;;
  worker)
    exec celery -A app.worker worker --loglevel=info --concurrency="${CELERY_CONCURRENCY:-2}"
    ;;
  beat)
    exec celery -A app.worker beat --loglevel=info
    ;;
  worker_crm_sync)
    exec celery -A app.worker worker -Q crm_sync --concurrency=1 --loglevel=info
    ;;
  worker_crm_ai)
    exec celery -A app.worker worker -Q crm-ai --concurrency=1 --loglevel=info
    ;;
  *)
    echo "entrypoint: unknown PROCESS_TYPE '${PROCESS_TYPE}'." >&2
    echo "            Expected one of: api, worker, beat, worker_crm_sync, worker_crm_ai." >&2
    exit 1
    ;;
esac
