FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1
ENV PATH="/app/.venv/bin:$PATH"

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install uv
RUN pip install uv

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock ./

# Install dependencies only (skip installing the project itself)
RUN uv sync --no-dev --no-install-project

# Copy application code
COPY app/ app/
COPY start_api.py entrypoint.sh ./
RUN chmod +x entrypoint.sh

# Now install the project (app/ exists at this point)
RUN uv sync --no-dev

EXPOSE 8000

# One image, three process types — see entrypoint.sh. PROCESS_TYPE selects
# which; unset means the API, so this stays a drop-in web image.
CMD ["./entrypoint.sh"]
