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

# Пока ребята не написали код, контейнер будет просто "висеть", чтобы не падал compose
CMD ["tail", "-f", "/dev/null"]
# В будущем заменить на: CMD ["/app/.venv/bin/python", "-m", "src.main"]