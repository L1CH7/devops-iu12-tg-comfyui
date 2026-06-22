#!/usr/bin/env python3
import socket
import urllib.request
import urllib.error
import sys
import argparse

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

def check_tcp_port(host, port, timeout=3):
    """Проверяет доступность TCP-порта"""
    # Поддержка IPv6 адресов (если адрес содержит двоеточия, socket требует семейство AF_INET6)
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    s = socket.socket(family, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        # Убираем квадратные скобки для socket.connect, если они переданы
        clean_host = host.replace("[", "").replace("]", "")
        s.connect((clean_host, port))
        s.close()
        return True, None
    except Exception as e:
        return False, str(e)

def check_http_endpoint(url, expected_code=200, timeout=3):
    """Проверяет HTTP-эндпоинт на код ответа"""
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'InfraTestClient/1.0'})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            code = response.getcode()
            if code == expected_code:
                return True, f"Status code {code}"
            return False, f"Expected {expected_code}, got {code}"
    except urllib.error.HTTPError as e:
        if e.code == expected_code:
            return True, f"Status code {e.code}"
        return False, f"HTTP Error {e.code}"
    except Exception as e:
        return False, str(e)

def run_pc1_tests(host):
    print(f"\n🚀 Запуск внешних проверок инфраструктуры ПК1 ({host})...")
    
    # 1. Проверка внешних портов
    ports_to_check = [
        ("Caddy (HTTP Proxy)", 80),
        ("ComfyUI (Direct)", 8188)
    ]
    
    for name, port in ports_to_check:
        ok, err = check_tcp_port(host, port)
        if ok:
            print_ok(f"Порт {port} ({name}) доступен по TCP")
        else:
            print_fail(f"Порт {port} ({name}) НЕДОСТУПЕН по TCP", err)

    # 2. Проверка HTTP-маршрутизации Caddy
    # Оформляем IPv6 в квадратные скобки для URL
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

def run_pc2_tests(host):
    print(f"\n📊 Запуск внешних проверок мониторинга ПК2 ({host})...")
    
    ports_to_check = [
        ("Grafana UI", 3000),
        ("Prometheus UI", 9090),
        ("Loki API", 3100)
    ]
    
    for name, port in ports_to_check:
        ok, err = check_tcp_port(host, port)
        if ok:
            print_ok(f"Порт {port} ({name}) доступен по TCP")
        else:
            print_fail(f"Порт {port} ({name}) НЕДОСТУПЕН по TCP", err)

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

def main():
    parser = argparse.ArgumentParser(description="Скрипт интеграционного тестирования инфраструктуры проекта.")
    parser.add_argument("--pc1", default="203:ddd5:7485:f6ac:90bd:ab97:cdbe:c177", help="IP-адрес или хост ПК1")
    parser.add_argument("--pc2", default="203:ddd5:7485:f6ac:90bd:ab97:cdbe:c177", help="IP-адрес или хост ПК2")
    parser.add_argument("--only-pc1", action="store_true", help="Запустить тесты только для ПК1")
    parser.add_argument("--only-pc2", action="store_true", help="Запустить тесты только для ПК2")
    
    args = parser.parse_args()

    if args.only_pc1:
        run_pc1_tests(args.pc1)
    elif args.only_pc2:
        run_pc2_tests(args.pc2)
    else:
        run_pc1_tests(args.pc1)
        run_pc2_tests(args.pc2)

if __name__ == "__main__":
    main()
