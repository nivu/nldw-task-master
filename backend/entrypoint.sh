#!/usr/bin/env sh
#
# Container entrypoint. One image, three process types.
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
  *)
    echo "entrypoint: unknown PROCESS_TYPE '${PROCESS_TYPE}'." >&2
    echo "            Expected one of: api, worker, beat." >&2
    exit 1
    ;;
esac
