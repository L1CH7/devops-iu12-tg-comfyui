from __future__ import annotations

import json
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import psycopg2
import redis
from psycopg2.extras import Json, RealDictCursor


QUEUE_KEY = "queue:generations"


def get_env(name: str, default: str) -> str:
    return os.getenv(name, default)


def get_pg_connection():
    return psycopg2.connect(
        host=get_env("POSTGRES_HOST", "postgres"),
        port=int(get_env("POSTGRES_PORT", "5432")),
        database=get_env("POSTGRES_DB", "tg_comfy_db"),
        user=get_env("POSTGRES_USER", "abm_admin"),
        password=get_env("POSTGRES_PASSWORD", "CHANGE_ME"),
    )


def get_redis_client() -> redis.Redis:
    return redis.Redis(
        host=get_env("REDIS_HOST", "redis"),
        port=int(get_env("REDIS_PORT", "6379")),
        password=get_env("REDIS_PASSWORD", "CHANGE_ME"),
        decode_responses=True,
    )


class ApiError(Exception):
    """Контролируемая ошибка API."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        self.message = message
        super().__init__(message)


class ApiHandler(BaseHTTPRequestHandler):
    """HTTP API для Telegram-бота и инфраструктурных проверок."""

    server_version = "tg-comfy-server-api/0.1"

    def do_GET(self) -> None:
        path = urlparse(self.path).path

        try:
            if path in {"/", "/health", "/api/health"}:
                self.handle_health()
                return

            if path.startswith("/api/status/"):
                task_id = path.removeprefix("/api/status/").strip("/")
                self.handle_status(task_id)
                return

            self.send_json(
                404,
                {
                    "status": "error",
                    "error": "not_found",
                    "message": f"Unknown endpoint: {path}",
                },
            )
        except ApiError as error:
            self.send_json(
                error.status_code,
                {
                    "status": "error",
                    "error": error.message,
                },
            )
        except Exception as error:
            self.send_json(
                500,
                {
                    "status": "error",
                    "error": "internal_server_error",
                    "message": str(error),
                },
            )

    def do_POST(self) -> None:
        path = urlparse(self.path).path

        try:
            if path == "/api/generate":
                self.handle_generate()
                return

            self.send_json(
                404,
                {
                    "status": "error",
                    "error": "not_found",
                    "message": f"Unknown endpoint: {path}",
                },
            )
        except ApiError as error:
            self.send_json(
                error.status_code,
                {
                    "status": "error",
                    "error": error.message,
                },
            )
        except Exception as error:
            self.send_json(
                500,
                {
                    "status": "error",
                    "error": "internal_server_error",
                    "message": str(error),
                },
            )

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def handle_health(self) -> None:
        """
        Healthcheck сервиса.

        Endpoint нужен для Caddy, Docker Compose, инфраструктурных тестов
        и быстрой проверки, что API-процесс жив.
        """

        dependencies = {
            "postgres": "unknown",
            "redis": "unknown",
        }

        try:
            conn = get_pg_connection()
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
                cur.fetchone()
            conn.close()
            dependencies["postgres"] = "ok"
        except Exception as error:
            dependencies["postgres"] = f"error: {error}"

        try:
            r = get_redis_client()
            r.ping()
            dependencies["redis"] = "ok"
        except Exception as error:
            dependencies["redis"] = f"error: {error}"

        self.send_json(
            200,
            {
                "status": "ok",
                "service": "server-api",
                "dependencies": dependencies,
            },
        )

    def handle_generate(self) -> None:
        """
        Создание задачи генерации.

        По архитектуре:
        tg-bot -> server-api -> PostgreSQL + Redis ZSET queue.
        """

        payload = self.read_json()

        telegram_user_id = payload.get("telegram_user_id")
        chat_id = payload.get("chat_id")
        prompt = str(payload.get("prompt", "")).strip()
        negative_prompt = payload.get("negative_prompt")
        username = payload.get("username")
        model_type = payload.get("model_type", "sdxl-refiner")

        if telegram_user_id is None:
            raise ApiError(400, "telegram_user_id is required")

        if chat_id is None:
            raise ApiError(400, "chat_id is required")

        if not prompt:
            raise ApiError(400, "prompt is required")

        if len(prompt) < 3:
            raise ApiError(400, "prompt is too short")

        task_id = str(uuid4())

        conn = get_pg_connection()
        redis_client = get_redis_client()

        try:
            with conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    user = self.get_or_create_user(
                        cur=cur,
                        telegram_user_id=int(telegram_user_id),
                        username=username,
                    )

                    self.validate_user_limits(cur, user)

                    inputs = {
                        "telegram_user_id": telegram_user_id,
                        "chat_id": chat_id,
                        "prompt": prompt,
                        "negative_prompt": negative_prompt,
                    }

                    cur.execute(
                        """
                        INSERT INTO generations (
                            id,
                            user_id,
                            model_type,
                            status,
                            negative_prompt,
                            inputs,
                            outputs
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s);
                        """,
                        (
                            task_id,
                            user["id"],
                            model_type,
                            "pending",
                            negative_prompt,
                            Json(inputs),
                            Json({}),
                        ),
                    )

                    score = self.calculate_queue_score(user["role"])
                    redis_client.zadd(QUEUE_KEY, {task_id: score})

                    rank = redis_client.zrank(QUEUE_KEY, task_id)
                    position = rank + 1 if rank is not None else None

            self.send_json(
                202,
                {
                    "task_id": task_id,
                    "status": "pending",
                    "position": position,
                },
            )
        finally:
            conn.close()

    def handle_status(self, task_id: str) -> None:
        """Получение статуса задачи генерации."""

        if not task_id:
            raise ApiError(400, "task_id is required")

        conn = get_pg_connection()
        redis_client = get_redis_client()

        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT
                        g.id,
                        g.status,
                        g.inputs,
                        g.outputs,
                        g.error_message,
                        g.created_at,
                        g.updated_at,
                        u.tg_id,
                        u.username,
                        u.role
                    FROM generations g
                    JOIN users u ON u.id = g.user_id
                    WHERE g.id = %s;
                    """,
                    (task_id,),
                )
                generation = cur.fetchone()

            if generation is None:
                raise ApiError(404, "generation not found")

            rank = redis_client.zrank(QUEUE_KEY, task_id)
            position = rank + 1 if rank is not None else None

            outputs = generation.get("outputs") or {}

            if isinstance(outputs, str):
                outputs = json.loads(outputs)

            result_url = (
                outputs.get("result_url")
                or outputs.get("image_url")
                or outputs.get("url")
            )

            self.send_json(
                200,
                {
                    "task_id": str(generation["id"]),
                    "status": generation["status"],
                    "position": position,
                    "result_url": result_url,
                    "error": generation.get("error_message"),
                },
            )
        finally:
            conn.close()

    @staticmethod
    def get_or_create_user(
        cur,
        telegram_user_id: int,
        username: str | None,
    ) -> dict[str, Any]:
        """
        Создаёт пользователя Telegram или обновляет username,
        если пользователь уже существует.
        """

        cur.execute(
            """
            INSERT INTO users (tg_id, username)
            VALUES (%s, %s)
            ON CONFLICT (tg_id)
            DO UPDATE SET username = COALESCE(EXCLUDED.username, users.username)
            RETURNING id, tg_id, username, role, daily_limit, cooldown_seconds;
            """,
            (telegram_user_id, username),
        )
        return dict(cur.fetchone())

    @staticmethod
    def validate_user_limits(cur, user: dict[str, Any]) -> None:
        """
        Проверяет daily limit и cooldown.

        Лимиты хранятся в таблице users:
        daily_limit — сколько генераций в день;
        cooldown_seconds — минимальная пауза между запросами.
        """

        cur.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM generations
            WHERE user_id = %s
              AND created_at >= CURRENT_DATE;
            """,
            (user["id"],),
        )
        daily_count = cur.fetchone()["cnt"]

        if daily_count >= user["daily_limit"]:
            raise ApiError(429, "daily generation limit exceeded")

        cur.execute(
            """
            SELECT EXTRACT(EPOCH FROM (NOW() - created_at)) AS seconds_since_last
            FROM generations
            WHERE user_id = %s
            ORDER BY created_at DESC
            LIMIT 1;
            """,
            (user["id"],),
        )
        row = cur.fetchone()

        if row and row["seconds_since_last"] is not None:
            seconds_since_last = float(row["seconds_since_last"])
            cooldown = int(user["cooldown_seconds"])

            if seconds_since_last < cooldown:
                retry_after = int(cooldown - seconds_since_last)
                raise ApiError(
                    429,
                    f"cooldown is active, retry after {retry_after} seconds",
                )

    @staticmethod
    def calculate_queue_score(role: str) -> float:
        """
        Вычисляет score для Redis ZSET.

        Обычные пользователи получают score = текущее время.
        VIP получает score меньше, поэтому задача оказывается выше в очереди.
        """

        score = time.time()

        if role == "vip":
            score -= 86400

        return score

    def read_json(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0"))

        if content_length <= 0:
            raise ApiError(400, "empty request body")

        raw_body = self.rfile.read(content_length)

        try:
            data = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError as error:
            raise ApiError(400, "invalid json body") from error

        if not isinstance(data, dict):
            raise ApiError(400, "json body must be an object")

        return data

    def send_json(self, status_code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")

        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(
            f"{self.address_string()} - {self.log_date_time_string()} "
            f"{fmt % args}"
        )


def main() -> None:
    server_address = ("", 8000)
    httpd = HTTPServer(server_address, ApiHandler)

    print("server-api started on port 8000")
    print("Available endpoints:")
    print("  GET  /api/health")
    print("  POST /api/generate")
    print("  GET  /api/status/{task_id}")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass

    print("server-api stopped")


if __name__ == "__main__":
    main()