# DevOps проект: Telegram-бот и AI-инфраструктура (МГТУ им. Баумана)

[Документация проекта](https://github.com/L1CH7/devops-iu12-tg-comfyui/wiki/%D0%94%D0%BE%D0%BA%D1%83%D0%BC%D0%B5%D0%BD%D1%82%D0%B0%D1%86%D0%B8%D1%8F)
Данный проект реализует полный цикл DevOps для системы генерации изображений.

## 1. Команда и роли
* **Шурлепов И.** (Infra Engineer) — Настройка сервера, Ansible, NGINX.
* **Иванов В.** (DevOps Lead) — Архитектура, CI/CD, релиз-менеджмент.
* **Расколотов Д.** (Developer / QA) — Логика бота, тесты, документация.

## 2. Технологический стек
* **CI/CD:** GitHub Actions.
* **Контейнеризация:** Docker + Docker Compose (Multi-stage builds).
* **Web:** Caddy + Telegram-bot + Yggdrasil.
* **Мониторинг:** Prometheus + Grafana + Loki (стек PLG).
* **Инфраструктура:** Redis, PostgreSQL, MinIO.

## 3. Правила разработки (GitFlow)
* Разработка ведётся только в ветках `feature/*`, отходящих от `develop`.
* Прямые пуши в `master` и `develop` запрещены.
* Для любого изменения обязателен Pull Request и Code Review (минимум 1 аппрув).
* Стандарт коммитов: **Conventional Commits** (feat, fix, docs, chore и т.д.).

## 4. Структура проекта
```text
.
├── compose/                # Конфиги Docker Compose для разных хостов
│   ├── pc1-server/         # Основной стек (БД, API, AI, Proxy)
│   └── pc2-monitor/        # Стек мониторинга (PLG, Prometheus)
├── services/               # Исходный код микросервисов
│   ├── tg-bot/             # Telegram-бот
│   └── server-api/         # Бэкенд
└── README.md
```

## 5. Быстрый запуск
Для запуска инфраструктуры используйте соответствующие директории:

**На основном сервере (ПК1):**
```bash
cd compose/pc1-server
cp .env.example .env
docker compose up -d --build
```

**На сервере мониторинга (ПК2):**
```bash
cd compose/pc2-monitor
cp .env.example .env
docker compose up -d --build
```
