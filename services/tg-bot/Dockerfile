# Multi-stage сборка с официальным образом uv
FROM ghcr.io/astral-sh/uv:python3.12-alpine AS builder
WORKDIR /app

# Копируем зависимости (когда ребята их создадут)
# COPY pyproject.toml uv.lock ./
# RUN uv sync --frozen --no-cache

# Финальный легковесный образ
FROM ghcr.io/astral-sh/uv:python3.12-alpine
WORKDIR /app
# COPY --from=builder /app/.venv /app/.venv
# COPY src/ ./src/

# Пока код и зависимости не добавлены, контейнер должен явно сигнализировать о неготовности
CMD ["sh", "-c", "echo 'tg-bot service is not ready: application code and/or dependencies are missing' >&2; exit 1"]
# В будущем заменить на: CMD ["/app/.venv/bin/python", "-m", "src.main"]