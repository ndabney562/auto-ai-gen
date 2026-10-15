# Build only from a trusted coordinator checkout, before any generated changes.
FROM python:3.12-slim
WORKDIR /opt/bootstrap
COPY pyproject.toml ./
COPY orchestrator ./orchestrator
COPY providers ./providers
RUN pip install --no-cache-dir '.[dev]'
WORKDIR /workspace
ENV PYTHONPATH=/workspace PYTHONDONTWRITEBYTECODE=1
USER 65534:65534
