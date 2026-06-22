#!/usr/bin/env python3
import os
import sys
import time
import uuid
import json
import urllib.request
import psycopg2
import redis
import boto3
from botocore.client import Config

# Цвета для вывода в консоль
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"

def print_ok(text):
    print(f"{GREEN}[OK] {text}{RESET}")

def print_fail(text, detail=None):
    err = f" ({detail})" if detail else ""
    print(f"{RED}[FAIL] {text}{err}{RESET}")

def print_warn(text):
    print(f"{YELLOW}[WARN] {text}{RESET}")

def load_env(env_path):
    """Парсит .env файл и возвращает словарь переменных"""
    env_vars = {}
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        k, v = line.split("=", 1)
                        env_vars[k.strip()] = v.strip().strip('"').strip("'")
        except Exception as e:
            print_warn(f"Не удалось прочитать {env_path}: {e}")
    return env_vars

def main():
    print("Запуск сквозных E2E-тестов инфраструктуры...")
    
    # 1. Загрузка переменных окружения
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pc1_env_path = os.path.join(base_dir, "compose", "pc1-server", ".env")
    env_vars = load_env(pc1_env_path)
    
    # Сливаем переменные из .env и os.environ
    for k, v in env_vars.items():
        if k not in os.environ:
            os.environ[k] = v

    # Определяем хосты
    # Если тест запущен внутри докер-контейнера, берем имена сервисов. Иначе localhost.
    in_docker = os.path.exists("/.dockerenv")
    
    pg_host = "postgres" if in_docker else "localhost"
    redis_host = "redis" if in_docker else "localhost"
    minio_host = "minio" if in_docker else "localhost"
    comfyui_host = "comfyui" if in_docker else "localhost"
    
    # Считываем конфиги БД и хранилищ
    db_name = os.environ.get("POSTGRES_DB", "app_db")
    db_user = os.environ.get("POSTGRES_USER", "app_user")
    db_pass = os.environ.get("POSTGRES_PASSWORD", "app_password")
    
    redis_pass = os.environ.get("REDIS_PASSWORD", "redis_password")
    
    minio_user = os.environ.get("MINIO_ROOT_USER", "minio_admin")
    minio_pass = os.environ.get("MINIO_ROOT_PASSWORD", "minio_password")
    
    success = True

    # ------------------ 2. ТЕСТИРОВАНИЕ POSTGRESQL ------------------
    print("\n--- Проверка PostgreSQL ---")
    conn = None
    try:
        conn = psycopg2.connect(
            host=pg_host,
            database=db_name,
            user=db_user,
            password=db_pass,
            port=5432
        )
        cur = conn.cursor()
        
        # Проверка базовой связи
        cur.execute("SELECT 1;")
        cur.fetchone()
        print_ok("Соединение с PostgreSQL успешно установлено")
        
        # Чистим старые тестовые данные
        cur.execute("DELETE FROM generations WHERE model_type = 'e2e-test-model';")
        cur.execute("DELETE FROM users WHERE tg_id IN (999999999, 888888888);")
        conn.commit()

        # Вставляем обычного пользователя
        cur.execute(
            "INSERT INTO users (tg_id, username, role, daily_limit, cooldown_seconds) VALUES (%s, %s, %s, %s, %s) RETURNING id;",
            (999999999, "e2e_user", "user", 10, 60)
        )
        user_db_id = cur.fetchone()[0]
        
        # Вставляем VIP пользователя
        cur.execute(
            "INSERT INTO users (tg_id, username, role, daily_limit, cooldown_seconds) VALUES (%s, %s, %s, %s, %s) RETURNING id;",
            (888888888, "e2e_vip", "vip", 100, 5)
        )
        vip_db_id = cur.fetchone()[0]
        print_ok("Тестовые пользователи (обычный и VIP) успешно созданы")
        
        # Вставляем генерацию
        gen_id = str(uuid.uuid4())
        inputs_json = json.dumps({"prompt": "sunset on Mars", "steps": 25})
        outputs_json = json.dumps({"image_url": "s3://generations/sunset.png"})
        
        cur.execute(
            "INSERT INTO generations (id, user_id, model_type, status, comfy_prompt_id, negative_prompt, inputs, outputs) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s);",
            (gen_id, user_db_id, "e2e-test-model", "completed", "comfy-prompt-12345", "ugly, blurry", inputs_json, outputs_json)
        )
        conn.commit()
        print_ok("Запись генерации с negative_prompt и comfy_prompt_id добавлена")
        
        # Выбираем обратно и проверяем
        cur.execute("SELECT id, comfy_prompt_id, negative_prompt, inputs, outputs FROM generations WHERE id = %s;", (gen_id,))
        row = cur.fetchone()
        assert row is not None, "Генерация не найдена в БД"
        assert row[1] == "comfy-prompt-12345", f"Некорректный comfy_prompt_id: {row[1]}"
        assert row[2] == "ugly, blurry", f"Некорректный negative_prompt: {row[2]}"
        assert row[3]["prompt"] == "sunset on Mars", f"Некорректный inputs: {row[3]}"
        assert row[4]["image_url"] == "s3://generations/sunset.png", f"Некорректный outputs: {row[4]}"
        print_ok("Данные генерации успешно верифицированы из БД")
        
        # Чистим данные
        cur.execute("DELETE FROM generations WHERE id = %s;", (gen_id,))
        cur.execute("DELETE FROM users WHERE id IN (%s, %s);", (user_db_id, vip_db_id))
        conn.commit()
        print_ok("Тестовые данные PostgreSQL успешно очищены")
        cur.close()
    except Exception as e:
        print_fail("Ошибка при тестировании PostgreSQL", str(e))
        success = False
    finally:
        if conn:
            conn.close()

    # ------------------ 3. ТЕСТИРОВАНИЕ REDIS (ZSET) ------------------
    print("\n--- Проверка Redis (Приоритетная очередь ZSET) ---")
    r = None
    try:
        r = redis.Redis(host=redis_host, port=6379, password=redis_pass, decode_responses=True)
        r.ping()
        print_ok("Соединение с Redis успешно установлено")
        
        queue_key = "test_queue:generations"
        r.delete(queue_key)
        
        # Тестируем приоритет. VIP должен обойти обычного.
        user_task_id = f"task:user:{uuid.uuid4()}"
        vip_task_id = f"task:vip:{uuid.uuid4()}"
        
        # Добавляем сначала обычную задачу (Score = текущее время)
        t_user = time.time()
        score_user = t_user
        r.zadd(queue_key, {user_task_id: score_user})
        print_ok(f"Обычная задача добавлена. Score = {score_user:.2f}")
        
        # Добавляем через 1 секунду VIP задачу (Score = текущее время - 86400 секунд скидки)
        time.sleep(1)
        t_vip = time.time()
        score_vip = t_vip - 86400  # VIP обгоняет
        r.zadd(queue_key, {vip_task_id: score_vip})
        print_ok(f"VIP-задача добавлена позже. Score = {score_vip:.2f} (со скидкой)")
        
        # Получаем элементы из очереди по возрастанию Score (кто первый на выполнение)
        queue_items = r.zrange(queue_key, 0, -1)
        print_ok(f"Текущее состояние очереди (первые элементы): {queue_items}")
        
        # Проверяем, что VIP-задача первая
        assert queue_items[0] == vip_task_id, "VIP-задача не обогнала обычную!"
        assert queue_items[1] == user_task_id, "Обычная задача не на своем месте!"
        print_ok("Приоритетное упорядочивание ZSET работает корректно (VIP впереди)")
        
        # Проверяем определение позиции через ZRANK
        # ZRANK возвращает индекс с 0, поэтому позиция = index + 1
        vip_rank = r.zrank(queue_key, vip_task_id)
        user_rank = r.zrank(queue_key, user_task_id)
        
        assert vip_rank == 0, f"Неверная позиция VIP: {vip_rank + 1}"
        assert user_rank == 1, f"Неверная позиция User: {user_rank + 1}"
        print_ok(f"Позиция VIP-задачи: {vip_rank + 1}, обычного пользователя: {user_rank + 1}")
        
        # Очистка очереди
        r.delete(queue_key)
        print_ok("Тестовые данные Redis успешно очищены")
    except Exception as e:
        print_fail("Ошибка при тестировании Redis", str(e))
        success = False

    # ------------------ 4. ТЕСТИРОВАНИЕ MINIO S3 ------------------
    print("\n--- Проверка MinIO S3 ---")
    try:
        s3 = boto3.client(
            's3',
            endpoint_url=f"http://{minio_host}:9000",
            aws_access_key_id=minio_user,
            aws_secret_access_key=minio_pass,
            config=Config(signature_version='s3v4')
        )
        
        # Проверка списка бакетов
        buckets = s3.list_buckets()
        bucket_names = [b['Name'] for b in buckets['Buckets']]
        print_ok(f"Список бакетов в MinIO: {bucket_names}")
        
        assert "generations" in bucket_names, "Бакет 'generations' отсутствует"
        print_ok("Бакет 'generations' найден")
        
        # Загрузка тестового файла
        test_key = "e2e_test_file.txt"
        test_data = b"E2E MinIO Upload and Integration Test Data"
        s3.put_object(Bucket="generations", Key=test_key, Body=test_data, ContentType="text/plain")
        print_ok(f"Файл '{test_key}' успешно загружен в бакет 'generations'")
        
        # Скачивание и сверка содержимого
        resp = s3.get_object(Bucket="generations", Key=test_key)
        downloaded_data = resp['Body'].read()
        assert downloaded_data == test_data, "Скачанные данные не совпадают с загруженными!"
        print_ok("Данные успешно скачаны и верифицированы")
        
        # Удаление тестового файла
        s3.delete_object(Bucket="generations", Key=test_key)
        print_ok(f"Файл '{test_key}' успешно удален из MinIO")
    except Exception as e:
        print_fail("Ошибка при тестировании MinIO S3", str(e))
        success = False

    # ------------------ 5. ТЕСТИРОВАНИЕ COMFYUI ------------------
    print("\n--- Проверка ComfyUI API ---")
    try:
        # Так как порт ComfyUI 8188 закрыт извне на ПК1 (доступ только через прокси caddy по пути /comfy/),
        # но внутри докер сети comfyui доступен напрямую по http://comfyui:8188/
        url = f"http://{comfyui_host}:8188/object_info"
        req = urllib.request.Request(url, headers={'User-Agent': 'E2ETestClient/1.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            content = response.read().decode('utf-8')
            if "sd_xl_refiner_1.0.safetensors" in content:
                print_ok("ComfyUI успешно видит модель 'sd_xl_refiner_1.0.safetensors'")
            else:
                print_fail("ComfyUI НЕ видит модель 'sd_xl_refiner_1.0.safetensors' в object_info")
                success = False
    except Exception as e:
        print_fail("Ошибка при тестировании ComfyUI API", str(e))
        success = False

    # ------------------ ФИНАЛЬНЫЙ РЕЗУЛЬТАТ ------------------
    print("\n=================================")
    if success:
        print(f"{GREEN}Сквозные E2E-тесты успешно пройдены!{RESET}")
        sys.exit(0)
    else:
        print(f"{RED}E2E-тесты провалились!{RESET}")
        sys.exit(1)

if __name__ == "__main__":
    main()
