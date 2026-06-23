import json
import os
import time
import uuid
import urllib.request
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

import psycopg2
import redis
import boto3
from botocore.client import Config
import urllib.parse


POSTGRES_HOST = os.getenv("POSTGRES_HOST", "postgres")
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_DB = os.getenv("POSTGRES_DB", "tg_comfy_db")
POSTGRES_USER = os.getenv("POSTGRES_USER", "abm_admin")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "")

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")
QUEUE_KEY = os.getenv("GENERATION_QUEUE_KEY", "queue:generations")

N8N_GENERATION_WEBHOOK_URL = os.getenv("N8N_GENERATION_WEBHOOK_URL", "")
PIPELINE_CALLBACK_URL = os.getenv(
    "PIPELINE_CALLBACK_URL",
    "http://server-api:8000/api/generation-result",
)
PIPELINE_CALLBACK_TOKEN = os.getenv("PIPELINE_CALLBACK_TOKEN", "")


MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "http://minio:9000")
MINIO_ROOT_USER = os.getenv("MINIO_ROOT_USER", "admin")
MINIO_ROOT_PASSWORD = os.getenv("MINIO_ROOT_PASSWORD", "supersecret_minio_password")
COMFYUI_URL = os.getenv("COMFYUI_URL", "http://comfyui:8188")


def generate_image_via_comfyui(prompt: str, negative_prompt: str | None, task_id: str) -> str:
    workflow = {
        "3": {
            "inputs": {
                "seed": int(time.time()),
                "steps": 25,
                "cfg": 7.0,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 1.0,
                "model": ["4", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0]
            },
            "class_type": "KSampler"
        },
        "4": {
            "inputs": {
                "ckpt_name": "sd_xl_refiner_1.0.safetensors"
            },
            "class_type": "CheckpointLoaderSimple"
        },
        "5": {
            "inputs": {
                "width": 1024,
                "height": 1024,
                "batch_size": 1
            },
            "class_type": "EmptyLatentImage"
        },
        "6": {
            "inputs": {
                "text": prompt,
                "clip": ["4", 1]
            },
            "class_type": "CLIPTextEncode"
        },
        "7": {
            "inputs": {
                "text": negative_prompt or "blurry, low quality, ugly, distorted",
                "clip": ["4", 1]
            },
            "class_type": "CLIPTextEncode"
        },
        "8": {
            "inputs": {
                "samples": ["3", 0],
                "vae": ["4", 2]
            },
            "class_type": "VAEDecode"
        },
        "9": {
            "inputs": {
                "filename_prefix": task_id,
                "images": ["8", 0]
            },
            "class_type": "SaveImage"
        }
    }

    prompt_data = {"prompt": workflow}
    body = json.dumps(prompt_data).encode("utf-8")
    
    req = urllib.request.Request(
        f"{COMFYUI_URL}/prompt",
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "server-api/1.0"}
    )
    
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            res = json.loads(response.read().decode("utf-8"))
            prompt_id = res["prompt_id"]
    except Exception as exc:
        raise RuntimeError(f"Failed to submit prompt to ComfyUI: {exc}")

    filename = None
    subfolder = None
    image_type = None
    
    for _ in range(60): # 120 seconds max timeout
        history_url = f"{COMFYUI_URL}/history/{prompt_id}"
        try:
            req = urllib.request.Request(history_url, headers={"User-Agent": "server-api/1.0"})
            with urllib.request.urlopen(req, timeout=5) as response:
                history = json.loads(response.read().decode("utf-8"))
                if prompt_id in history:
                    outputs = history[prompt_id]["outputs"]
                    for node_id in outputs:
                        if "images" in outputs[node_id]:
                            img_info = outputs[node_id]["images"][0]
                            filename = img_info["filename"]
                            subfolder = img_info["subfolder"]
                            image_type = img_info["type"]
                    break
        except Exception:
            pass
        time.sleep(2)

    if not filename:
        raise RuntimeError("ComfyUI generation timed out or failed")

    params = urllib.parse.urlencode({
        "filename": filename,
        "subfolder": subfolder or "",
        "type": image_type or "output"
    })
    view_url = f"{COMFYUI_URL}/view?{params}"
    
    try:
        req = urllib.request.Request(view_url, headers={"User-Agent": "server-api/1.0"})
        with urllib.request.urlopen(req, timeout=15) as response:
            image_bytes = response.read()
    except Exception as exc:
        raise RuntimeError(f"Failed to download image from ComfyUI: {exc}")

    try:
        s3 = boto3.client(
            "s3",
            endpoint_url=MINIO_ENDPOINT,
            aws_access_key_id=MINIO_ROOT_USER,
            aws_secret_access_key=MINIO_ROOT_PASSWORD,
            config=Config(signature_version="s3v4")
        )
        s3_key = f"{task_id}.png"
        s3.put_object(
            Bucket="generations",
            Key=s3_key,
            Body=image_bytes,
            ContentType="image/png"
        )
    except Exception as exc:
        raise RuntimeError(f"Failed to upload image to MinIO: {exc}")

    return f"http://127.0.0.1:9000/generations/{s3_key}"


def update_task_status(
    task_id: str,
    status: str,
    result_url: str | None = None,
    comfy_prompt_id: str | None = None,
    error_message: str | None = None,
    extra_outputs: dict | None = None
) -> None:
    outputs = {}
    if result_url:
        outputs["result_url"] = result_url
    if extra_outputs:
        outputs.update(extra_outputs)

    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE generations
                SET
                    status = %s,
                    comfy_prompt_id = COALESCE(%s, comfy_prompt_id),
                    outputs = %s::jsonb,
                    error_message = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                RETURNING id;
                """,
                (
                    status,
                    comfy_prompt_id,
                    json.dumps(outputs, ensure_ascii=False),
                    error_message,
                    task_id,
                ),
            )
            updated = cur.fetchone()
            if updated is None:
                raise ApiError(404, f"generation task not found: {task_id}")
        conn.commit()

    if status in {"completed", "failed"}:
        try:
            redis_client = get_redis_client()
            redis_client.zrem(QUEUE_KEY, task_id)
        except Exception as exc:
            print(f"Failed to remove task from Redis: {exc}", flush=True)


def queue_worker() -> None:
    print("Queue worker thread started", flush=True)
    redis_client = get_redis_client()
    while True:
        try:
            # Get the task with the lowest score (highest priority)
            tasks = redis_client.zrange(QUEUE_KEY, 0, 0)
            if not tasks:
                time.sleep(1)
                continue

            task_id = tasks[0]

            # Try to claim the task by updating its status to 'processing'
            with get_pg_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE generations
                        SET status = 'processing', updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s AND status = 'pending'
                        RETURNING id, inputs, negative_prompt;
                        """,
                        (task_id,),
                    )
                    row = cur.fetchone()
                conn.commit()

            if not row:
                # Task was already claimed or processed, remove it from Redis queue
                try:
                    redis_client.zrem(QUEUE_KEY, task_id)
                except Exception:
                    pass
                continue

            # We successfully claimed the task!
            inputs = parse_jsonb(row[1])
            prompt = inputs.get("prompt", "")
            negative_prompt = row[2]

            print(f"Worker processing task {task_id}: prompt='{prompt}'", flush=True)

            try:
                # Run ComfyUI generation
                result_url = generate_image_via_comfyui(prompt, negative_prompt, task_id)
                
                # Update task to completed
                update_task_status(task_id, "completed", result_url=result_url)
                print(f"Worker completed task {task_id}: {result_url}", flush=True)
            except Exception as exc:
                print(f"Worker failed task {task_id}: {exc}", flush=True)
                update_task_status(task_id, "failed", error_message=str(exc))

        except Exception as err:
            print(f"Queue worker error: {err}", flush=True)
            time.sleep(2)


class ApiError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def get_pg_connection():
    return psycopg2.connect(
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        database=POSTGRES_DB,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
    )


def get_redis_client():
    return redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=REDIS_PASSWORD,
        decode_responses=True,
    )


def send_json(handler: BaseHTTPRequestHandler, status_code: int, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")

    handler.send_response(status_code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type, X-Pipeline-Token")
    handler.end_headers()
    handler.wfile.write(body)


def read_json_body(handler: BaseHTTPRequestHandler) -> dict:
    content_length = int(handler.headers.get("Content-Length", "0"))

    if content_length <= 0:
        return {}

    raw_body = handler.rfile.read(content_length)

    try:
        return json.loads(raw_body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ApiError(400, f"Invalid JSON body: {exc}") from exc


def parse_jsonb(value):
    if value is None:
        return {}

    if isinstance(value, dict):
        return value

    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {}

    return {}


def trigger_n8n_pipeline(task_payload: dict) -> None:
    """
    Вызывает n8n webhook после создания задачи.

    Если n8n временно недоступен, /api/generate всё равно должен вернуть task_id,
    потому что задача уже записана в PostgreSQL и Redis.
    """

    if not N8N_GENERATION_WEBHOOK_URL:
        print("N8N_GENERATION_WEBHOOK_URL is empty, n8n trigger skipped", flush=True)
        return

    body = json.dumps(task_payload, ensure_ascii=False).encode("utf-8")

    request = urllib.request.Request(
        N8N_GENERATION_WEBHOOK_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "server-api/1.0",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            response_body = response.read().decode("utf-8", errors="ignore")
            print(
                "n8n webhook called successfully: "
                f"status={response.status}, body={response_body[:300]}",
                flush=True,
            )
    except Exception as exc:
        print(f"Failed to call n8n webhook: {exc}", flush=True)


class ApiHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        send_json(self, 204, {})

    def do_GET(self):
        parsed_path = urlparse(self.path)
        path = parsed_path.path.rstrip("/") or "/"

        try:
            if path in {"/", "/health", "/api/health"}:
                self.handle_health()
                return

            if path.startswith("/api/status/"):
                task_id = path.removeprefix("/api/status/").strip("/")
                self.handle_status(task_id)
                return

            send_json(
                self,
                404,
                {
                    "error": "not_found",
                    "message": f"Unknown path: {path}",
                },
            )

        except ApiError as exc:
            send_json(self, exc.status_code, {"error": exc.message})
        except Exception as exc:
            print(f"Unexpected GET error: {exc}", flush=True)
            send_json(
                self,
                500,
                {
                    "error": "internal_server_error",
                    "message": str(exc),
                },
            )

    def do_POST(self):
        parsed_path = urlparse(self.path)
        path = parsed_path.path.rstrip("/") or "/"

        try:
            if path == "/api/generate":
                self.handle_generate()
                return

            if path == "/api/generation-result":
                self.handle_generation_result()
                return

            send_json(
                self,
                404,
                {
                    "error": "not_found",
                    "message": f"Unknown path: {path}",
                },
            )

        except ApiError as exc:
            send_json(self, exc.status_code, {"error": exc.message})
        except Exception as exc:
            print(f"Unexpected POST error: {exc}", flush=True)
            send_json(
                self,
                500,
                {
                    "error": "internal_server_error",
                    "message": str(exc),
                },
            )

    def handle_health(self) -> None:
        dependencies = {}

        try:
            with get_pg_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1;")
                    cur.fetchone()
            dependencies["postgres"] = "ok"
        except Exception as exc:
            dependencies["postgres"] = f"error: {exc}"

        try:
            redis_client = get_redis_client()
            redis_client.ping()
            dependencies["redis"] = "ok"
        except Exception as exc:
            dependencies["redis"] = f"error: {exc}"

        send_json(
            self,
            200,
            {
                "status": "ok",
                "service": "server-api",
                "dependencies": dependencies,
                "n8n_webhook_configured": bool(N8N_GENERATION_WEBHOOK_URL),
            },
        )

    def handle_generate(self) -> None:
        payload = read_json_body(self)

        telegram_user_id = payload.get("telegram_user_id")
        chat_id = payload.get("chat_id")
        username = payload.get("username")
        prompt = str(payload.get("prompt", "")).strip()
        negative_prompt = payload.get("negative_prompt")
        model_type = str(payload.get("model_type", "sdxl")).strip() or "sdxl"

        if telegram_user_id is None:
            raise ApiError(400, "telegram_user_id is required")

        if chat_id is None:
            raise ApiError(400, "chat_id is required")

        if not prompt:
            raise ApiError(400, "prompt is required")

        try:
            telegram_user_id = int(telegram_user_id)
            chat_id = int(chat_id)
        except ValueError as exc:
            raise ApiError(400, "telegram_user_id and chat_id must be integers") from exc

        task_id = str(uuid.uuid4())

        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                user_id, role, daily_limit, cooldown_seconds = self.get_or_create_user(
                    cur=cur,
                    telegram_user_id=telegram_user_id,
                    username=username,
                )

                self.validate_user_limits(
                    cur=cur,
                    user_id=user_id,
                    daily_limit=daily_limit,
                    cooldown_seconds=cooldown_seconds,
                )

                inputs = {
                    "telegram_user_id": telegram_user_id,
                    "chat_id": chat_id,
                    "username": username,
                    "prompt": prompt,
                }

                if negative_prompt:
                    inputs["negative_prompt"] = negative_prompt

                cur.execute(
                    """
                    INSERT INTO generations (
                        id,
                        user_id,
                        workflow_id,
                        model_type,
                        status,
                        comfy_prompt_id,
                        negative_prompt,
                        inputs,
                        outputs,
                        error_message
                    )
                    VALUES (
                        %s,
                        %s,
                        NULL,
                        %s,
                        'pending',
                        NULL,
                        %s,
                        %s::jsonb,
                        '{}'::jsonb,
                        NULL
                    );
                    """,
                    (
                        task_id,
                        user_id,
                        model_type,
                        negative_prompt,
                        json.dumps(inputs, ensure_ascii=False),
                    ),
                )

            conn.commit()

        redis_client = get_redis_client()
        score = time.time()

        if role == "vip":
            score -= 86400

        redis_client.zadd(QUEUE_KEY, {task_id: score})
        rank = redis_client.zrank(QUEUE_KEY, task_id)
        position = int(rank) + 1 if rank is not None else None



        send_json(
            self,
            202,
            {
                "task_id": task_id,
                "status": "pending",
                "position": position,
            },
        )

    def get_or_create_user(
        self,
        cur,
        telegram_user_id: int,
        username: str | None,
    ) -> tuple[int, str, int, int]:
        cur.execute(
            """
            SELECT id, role, daily_limit, cooldown_seconds
            FROM users
            WHERE tg_id = %s;
            """,
            (telegram_user_id,),
        )
        row = cur.fetchone()

        if row:
            return int(row[0]), str(row[1]), int(row[2]), int(row[3])

        cur.execute(
            """
            INSERT INTO users (tg_id, username)
            VALUES (%s, %s)
            RETURNING id, role, daily_limit, cooldown_seconds;
            """,
            (telegram_user_id, username),
        )
        row = cur.fetchone()

        return int(row[0]), str(row[1]), int(row[2]), int(row[3])

    def validate_user_limits(
        self,
        cur,
        user_id: int,
        daily_limit: int,
        cooldown_seconds: int,
    ) -> None:
        cur.execute(
            """
            SELECT COUNT(*)
            FROM generations
            WHERE user_id = %s
              AND created_at >= CURRENT_DATE
              AND status <> 'failed';
            """,
            (user_id,),
        )
        daily_count = int(cur.fetchone()[0])

        if daily_count >= daily_limit:
            raise ApiError(
                429,
                f"Daily generation limit exceeded: {daily_count}/{daily_limit}",
            )

        cur.execute(
            """
            SELECT EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - created_at))
            FROM generations
            WHERE user_id = %s
            ORDER BY created_at DESC
            LIMIT 1;
            """,
            (user_id,),
        )
        row = cur.fetchone()

        if row and row[0] is not None:
            seconds_since_last = float(row[0])

            if seconds_since_last < cooldown_seconds:
                wait_seconds = int(cooldown_seconds - seconds_since_last)
                raise ApiError(
                    429,
                    f"Cooldown is active. Try again in {wait_seconds} seconds",
                )

    def handle_status(self, task_id: str) -> None:
        if not task_id:
            raise ApiError(400, "task_id is required")

        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        id,
                        status,
                        outputs,
                        error_message,
                        comfy_prompt_id
                    FROM generations
                    WHERE id = %s;
                    """,
                    (task_id,),
                )
                row = cur.fetchone()

        if row is None:
            raise ApiError(404, f"generation task not found: {task_id}")

        outputs = parse_jsonb(row[2])
        result_url = outputs.get("result_url") or outputs.get("url")

        position = None

        try:
            redis_client = get_redis_client()
            rank = redis_client.zrank(QUEUE_KEY, task_id)
            position = int(rank) + 1 if rank is not None else None
        except Exception as exc:
            print(f"Failed to get Redis queue position: {exc}", flush=True)

        send_json(
            self,
            200,
            {
                "task_id": str(row[0]),
                "status": row[1],
                "position": position,
                "result_url": result_url,
                "outputs": outputs,
                "error": row[3],
                "comfy_prompt_id": row[4],
            },
        )

    def handle_generation_result(self) -> None:
        payload = read_json_body(self)

        if PIPELINE_CALLBACK_TOKEN:
            header_token = self.headers.get("X-Pipeline-Token", "")
            body_token = str(payload.get("callback_token", ""))

            if (
                header_token != PIPELINE_CALLBACK_TOKEN
                and body_token != PIPELINE_CALLBACK_TOKEN
            ):
                raise ApiError(403, "Invalid pipeline callback token")

        task_id = str(payload.get("task_id", "")).strip()
        status = str(payload.get("status", "")).strip().lower()
        result_url = payload.get("result_url")
        comfy_prompt_id = payload.get("comfy_prompt_id")
        error_message = payload.get("error") or payload.get("error_message")

        if not task_id:
            raise ApiError(400, "task_id is required")

        if status not in {"processing", "completed", "failed"}:
            raise ApiError(400, "status must be one of: processing, completed, failed")

        if status == "completed" and not result_url:
            raise ApiError(400, "result_url is required for completed status")

        update_task_status(
            task_id=task_id,
            status=status,
            result_url=result_url,
            comfy_prompt_id=comfy_prompt_id,
            error_message=error_message,
            extra_outputs=payload.get("outputs"),
        )

        send_json(
            self,
            200,
            {
                "ok": True,
                "task_id": task_id,
                "status": status,
                "result_url": result_url,
            },
        )

    def log_message(self, format, *args):
        print(
            "%s - - [%s] %s"
            % (self.address_string(), self.log_date_time_string(), format % args),
            flush=True,
        )


def main():
    # Запуск фонового воркера очереди
    threading.Thread(target=queue_worker, daemon=True).start()

    server_address = ("", 8000)
    httpd = HTTPServer(server_address, ApiHandler)

    print("server-api started on port 8000", flush=True)
    print("Available endpoints:", flush=True)
    print("  GET  /api/health", flush=True)
    print("  POST /api/generate", flush=True)
    print("  GET  /api/status/{task_id}", flush=True)
    print("  POST /api/generation-result", flush=True)

    httpd.serve_forever()


if __name__ == "__main__":
    main()