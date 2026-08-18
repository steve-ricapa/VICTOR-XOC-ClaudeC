FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p \
    config/secrets \
    runtime/runs \
    runtime/workspaces \
    runtime/checkpoints \
    runtime/audit \
    runtime/decisions \
    runtime/artifacts \
    runtime/locks \
    && for file in agent policy capabilities mcp; do \
        if [ ! -f "config/${file}.yaml" ] && [ -f "config.dist/${file}.yaml" ]; then \
            cp "config.dist/${file}.yaml" "config/${file}.yaml"; \
        fi; \
    done \
    && useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE ${PORT}

CMD ["python", "victor_server.py"]
