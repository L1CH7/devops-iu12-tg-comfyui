#!/usr/bin/env python3
import socket
import urllib.request
import urllib.error
import sys
import os

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
                        # Очищаем от кавычек и лишних пробелов
                        env_vars[k.strip()] = v.strip().strip('"').strip("'")
        except Exception as e:
            print_warn(f"Не удалось прочитать {env_path}: {e}")
    return env_vars

def check_tcp_port(host, port, timeout=3):
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    s = socket.socket(family, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        clean_host = host.replace("[", "").replace("]", "")
        s.connect((clean_host, port))
        s.close()
        return True, None
    except Exception as e:
        return False, str(e)

import time

def check_http_endpoint(url, expected_code=200, timeout=3, retries=5, delay=2):
    last_err = ""
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'InfraTestClient/1.0'})
            with urllib.request.urlopen(req, timeout=timeout) as response:
                code = response.getcode()
                if code == expected_code:
                    return True, f"Status code {code}"
                last_err = f"Expected {expected_code}, got {code}"
        except urllib.error.HTTPError as e:
            if e.code == expected_code:
                return True, f"Status code {e.code}"
            last_err = f"HTTP Error {e.code}"
        except Exception as e:
            last_err = str(e)
        
        if attempt < retries:
            time.sleep(delay)
            
    return False, f"{last_err} (after {retries} attempts)"

def run_pc1_tests(host):
    print(f"\nЗапуск проверок инфраструктуры ПК1 ({host})...")
    
    ports_to_check = [
        ("Caddy (HTTP Proxy)", 80)
    ]
    
    success = True
    for name, port in ports_to_check:
        ok, err = check_tcp_port(host, port)
        if ok:
            print_ok(f"Порт {port} ({name}) доступен по TCP")
        else:
            print_fail(f"Порт {port} ({name}) НЕДОСТУПЕН по TCP", err)
            success = False

    url_host = f"[{host}]" if ":" in host else host
    
    endpoints = [
        (f"http://{url_host}/", 200, "Caddy Fallback / API"),
        (f"http://{url_host}/comfy/", 200, "ComfyUI via Caddy"),
        (f"http://{url_host}/api/health", 200, "server-api via Caddy"),
        (f"http://{url_host}/n8n/", 200, "n8n via Caddy")
    ]

    for url, code, name in endpoints:
        ok, detail = check_http_endpoint(url, expected_code=code)
        if ok:
            print_ok(f"Эндпоинт {name} ({url}) отвечает {code}")
        else:
            print_fail(f"Эндпоинт {name} ({url}) выдает ошибку", detail)
            success = False

    # 3. Интеграционный тест: проверяем, что ComfyUI видит смонтированную модель
    model_url = f"http://{url_host}/comfy/object_info"
    try:
        req = urllib.request.Request(model_url, headers={'User-Agent': 'InfraTestClient/1.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            content = response.read().decode('utf-8')
            if "sd_xl_refiner_1.0.safetensors" in content:
                print_ok("ComfyUI успешно обнаружил смонтированную модель sd_xl_refiner_1.0.safetensors")
            else:
                print_fail("ComfyUI НЕ видит модель sd_xl_refiner_1.0.safetensors в папке моделей")
                success = False
    except Exception as e:
        print_fail("Не удалось проверить список моделей ComfyUI через API", str(e))
        success = False
            
    return success

def run_pc2_tests(host):
    print(f"\nЗапуск проверок мониторинга ПК2 ({host})...")
    
    ports_to_check = [
        ("Grafana UI", 3000),
        ("Prometheus UI", 9090),
        ("Loki API", 3100)
    ]
    
    success = True
    for name, port in ports_to_check:
        ok, err = check_tcp_port(host, port)
        if ok:
            print_ok(f"Порт {port} ({name}) доступен по TCP")
        else:
            print_fail(f"Порт {port} ({name}) НЕДОСТУПЕН по TCP", err)
            success = False

    url_host = f"[{host}]" if ":" in host else host
    
    endpoints = [
        (f"http://{url_host}:3000/login", 200, "Grafana Login Page"),
        (f"http://{url_host}:9090/-/healthy", 200, "Prometheus Health Check"),
        (f"http://{url_host}:3100/ready", 200, "Loki Ready Check")
    ]

    for url, code, name in endpoints:
        ok, detail = check_http_endpoint(url, expected_code=code)
        if ok:
            print_ok(f"Эндпоинт {name} ({url}) отвечает {code}")
        else:
            print_fail(f"Эндпоинт {name} ({url}) выдает ошибку", detail)
            success = False
            
    return success

def main():
    # Определяем пути к .env файлам относительно корня проекта
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pc1_env_path = os.path.join(base_dir, "compose", "pc1-server", ".env")
    pc2_env_path = os.path.join(base_dir, "compose", "pc2-monitor", ".env")

    # Читаем переменные из файлов
    pc1_env = load_env(pc1_env_path)
    pc2_env = load_env(pc2_env_path)

    # Приоритет: 1. Системные env-переменные, 2. Значения из .env файлов, 3. Дефолт (localhost)
    pc1_host = os.environ.get("PC1_YGG_IP") or pc1_env.get("PC1_YGG_IP") or "localhost"
    
    # Для ПК2 ищем PC2_YGG_IP, либо PC2_LAN_IP, либо дефолт
    pc2_host = os.environ.get("PC2_YGG_IP") or pc2_env.get("PC2_YGG_IP") or "localhost"

    # Если запускается в CI/CD на конкретном раннере, проверяем только этот хост
    run_pc1 = True
    run_pc2 = True

    # Если передан аргумент командной строки для запуска конкретного набора тестов
    if len(sys.argv) > 1:
        if "--only-pc1" in sys.argv or "--pc1" in sys.argv:
            run_pc2 = False
        elif "--only-pc2" in sys.argv or "--pc2" in sys.argv:
            run_pc1 = False

    pc1_success = True
    pc2_success = True

    if run_pc1:
        pc1_success = run_pc1_tests(pc1_host)
        
    if run_pc2:
        if pc2_host != "localhost" or "--only-pc2" in sys.argv or "--pc2" in sys.argv:
            pc2_success = run_pc2_tests(pc2_host)
        else:
            print("\nПроверка ПК2 пропущена (хост настроен как localhost, используйте --only-pc2 для принудительного запуска)")

    if not pc1_success or not pc2_success:
        print(f"\n{RED}Инфраструктурные тесты завалились!{RESET}")
        sys.exit(1)
        
    print(f"\n{GREEN}Все инфраструктурные тесты успешно пройдены!{RESET}")
    sys.exit(0)

if __name__ == "__main__":
    main()
