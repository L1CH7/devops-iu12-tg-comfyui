# Multi-stage сборка с официальным образом uv
FROM ghcr.io/astral-sh/uv:python3.12-alpine AS builder
WORKDIR /app

# Копируем зависимости
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-cache

# Финальный легковесный образ
FROM ghcr.io/astral-sh/uv:python3.12-alpine
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY src/ ./src/

CMD ["/app/.venv/bin/python", "-m", "src.main"]