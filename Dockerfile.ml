FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PCB_QMS_DATA_DIR=/app/data
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libglib2.0-0 libgl1 && rm -rf /var/lib/apt/lists/*
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --index-url https://download.pytorch.org/whl/cpu torch==2.8.0 torchvision==0.23.0
COPY pyproject.toml README.md LICENSE ./
COPY app ./app
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install ".[anomalib]"
COPY scripts ./scripts
COPY alembic.ini ./
COPY migrations ./migrations
RUN useradd --create-home --uid 10001 qms && mkdir -p /app/data && chown -R qms:qms /app
USER qms
CMD ["python", "-m", "app.worker"]
