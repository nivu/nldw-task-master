.PHONY: help worker-start worker-stop worker-restart worker-status docker-up docker-down docker-restart-worker install

help:
	@echo "Available commands:"
	@echo "  make worker-start          - Start Celery worker + beat scheduler"
	@echo "  make worker-stop           - Stop Celery worker"
	@echo "  make worker-restart        - Restart Celery worker"
	@echo "  make worker-status         - Check worker status"
	@echo "  make docker-up             - Start all services with Docker Compose"
	@echo "  make docker-down           - Stop all Docker services"
	@echo "  make docker-restart-worker - Restart worker container"
	@echo "  make install               - Install dependencies"

# Starts BOTH processes. The worker alone is not enough: the nightly
# auto-approval sweep (spec Q-04) is a beat schedule, so without beat, pending
# bookings whose date has passed stay pending forever.
worker-start:
	@echo "Starting Celery worker and beat..."
	@if [ -f .venv/bin/activate ]; then \
		. .venv/bin/activate && celery -A app.worker worker --loglevel=info --concurrency=4 & \
		. .venv/bin/activate && celery -A app.worker beat --loglevel=info & \
	else \
		celery -A app.worker worker --loglevel=info --concurrency=4 & \
		celery -A app.worker beat --loglevel=info & \
	fi

worker-stop:
	@echo "Stopping Celery worker..."
	@pkill -f "celery.*app.worker" || echo "No worker or beat process found"

worker-restart: worker-stop
	@sleep 2
	@$(MAKE) worker-start

worker-status:
	@pgrep -f "celery.*app.worker" > /dev/null && \
		echo "✓ Celery worker is running (PID: $$(pgrep -f 'celery.*app.worker'))" || \
		echo "✗ Celery worker is NOT running"

docker-up:
	docker compose up -d
	@echo "Services started. Check logs with: docker compose logs -f"

docker-down:
	docker compose down

docker-restart-worker:
	docker compose restart worker
	@echo "Worker restarted. Check logs with: docker compose logs -f worker"

docker-logs:
	docker compose logs -f worker

install:
	@if command -v uv > /dev/null; then \
		uv sync; \
	else \
		pip install -e .; \
	fi
