# Telegram Bot Service

`tg-bot` — микросервис Telegram-бота для пользовательского взаимодействия с системой генерации изображений.

Бот принимает команды от пользователя, отправляет запросы в `server-api`, получает идентификатор задачи генерации, показывает позицию в очереди и позволяет проверить статус выполнения задачи.

## Место сервиса в архитектуре

Общий сценарий работы:

```text
Telegram User
    ↓
tg-bot
    ↓
server-api
    ↓
PostgreSQL / Redis Queue / Worker / ComfyUI / MinIO
```

`tg-bot` не обращается напрямую к Redis, PostgreSQL, MinIO или ComfyUI.
Он работает только через HTTP API сервиса `server-api`.

Такое разделение ответственности позволяет оставить Telegram-бот тонким клиентом, а бизнес-логику генерации, очередей, лимитов и хранения результатов держать на стороне backend.

## Основные возможности

Бот поддерживает команды:

| Команда                                           | Назначение                            |
| ------------------------------------------------- | ------------------------------------- |
| `/start`                                          | Приветствие и краткое описание бота   |
| `/help`                                           | Инструкция по использованию           |
| `/generate <prompt>`                              | Создание задачи генерации изображения |
| `/generate <prompt> --negative <negative_prompt>` | Создание задачи с negative prompt     |
| `/status <task_id>`                               | Проверка статуса задачи генерации     |

## Пример использования

Создание задачи генерации:

```text
/generate кот-космонавт на Марсе
```

Создание задачи с negative prompt:

```text
/generate cyberpunk city at night --negative blur, low quality
```

Проверка статуса задачи:

```text
/status task-123
```

## Переменные окружения

Сервис конфигурируется через переменные окружения.

| Переменная                 | Значение по умолчанию    | Описание                                   |
| -------------------------- | ------------------------ | ------------------------------------------ |
| `TELEGRAM_BOT_TOKEN`       | пусто                    | Токен Telegram-бота                        |
| `SERVER_API_URL`           | `http://server-api:8000` | URL backend API внутри Docker Compose сети |
| `GENERATE_ENDPOINT`        | `/api/generate`          | Endpoint создания задачи генерации         |
| `STATUS_ENDPOINT_TEMPLATE` | `/api/status/{task_id}`  | Endpoint проверки статуса задачи           |
| `REQUEST_TIMEOUT_SECONDS`  | `15.0`                   | Timeout HTTP-запросов к `server-api`       |
| `POLLING_INTERVAL_SECONDS` | `5.0`                    | Интервал для внутренних polling-сценариев  |

Важно: `TELEGRAM_BOT_TOKEN` нельзя хранить в коде и коммитить в Git.
Токен должен передаваться через `.env`, Docker Compose environment или секреты CI/CD.

## API-контракт с server-api

### Создание задачи генерации

Запрос:

```http
POST /api/generate
Content-Type: application/json
```

Тело запроса:

```json
{
  "telegram_user_id": 123456789,
  "chat_id": 123456789,
  "prompt": "cat astronaut on Mars",
  "negative_prompt": "blur, low quality"
}
```

Ожидаемый ответ:

```json
{
  "task_id": "task-123",
  "status": "pending",
  "position": 3
}
```

`position` — позиция задачи в Redis-очереди.
На стороне backend она может вычисляться через Redis ZSET и команду `ZRANK`.

### Получение статуса задачи

Запрос:

```http
GET /api/status/task-123
```

Ожидаемый ответ для задачи в очереди:

```json
{
  "task_id": "task-123",
  "status": "pending",
  "position": 3
}
```

Ожидаемый ответ для завершённой задачи:

```json
{
  "task_id": "task-123",
  "status": "completed",
  "result_url": "http://minio:9000/generations/task-123.png"
}
```

Ожидаемый ответ при ошибке генерации:

```json
{
  "task_id": "task-123",
  "status": "failed",
  "error": "generation failed"
}
```

## Структура сервиса

```text
services/tg-bot/
├── src/
│   ├── __init__.py
│   ├── api_client.py
│   ├── config.py
│   └── main.py
├── tests/
│   ├── conftest.py
│   └── test_api_client.py
├── Dockerfile
├── pyproject.toml
├── uv.lock
└── README.md
```

Назначение основных файлов:

| Файл                       | Назначение                                       |
| -------------------------- | ------------------------------------------------ |
| `src/main.py`              | Точка входа в Telegram-бота и обработчики команд |
| `src/config.py`            | Загрузка настроек из переменных окружения        |
| `src/api_client.py`        | HTTP-клиент для обмена с `server-api`            |
| `tests/test_api_client.py` | Тесты API-клиента                                |
| `tests/conftest.py`        | Настройка путей импорта для pytest               |
| `pyproject.toml`           | Описание Python-проекта и зависимостей           |
| `uv.lock`                  | Зафиксированные версии зависимостей              |

## Локальная разработка

Перейти в директорию сервиса:

```powershell
cd services/tg-bot
```

Синхронизировать зависимости:

```powershell
uv sync
```

Запустить проверку синтаксиса:

```powershell
uv run python -m compileall src
```

Запустить тесты:

```powershell
uv run pytest -q
```

## Запуск через Docker Compose

Обычно сервис запускается из общего Compose-файла основного сервера:

```powershell
cd compose/pc1-server
cp .env.example .env
docker compose up -d --build
```

В `.env` должен быть указан токен Telegram-бота:

```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
```

Внутри Docker Compose сети бот обращается к backend по адресу:

```text
http://server-api:8000
```

## Тестирование

Тесты API-клиента запускаются командой:

```powershell
uv run pytest -q
```

Текущие тесты проверяют:

* создание задачи генерации через `POST /api/generate`;
* получение статуса задачи через `GET /api/status/{task_id}`;
* обработку позиции в очереди;
* поддержку разных вариантов ID задачи: `task_id`, `job_id`, `id`;
* обработку ошибки `server-api`.

HTTP-запросы в тестах мокируются через `respx`, поэтому для запуска тестов не требуется поднимать настоящий `server-api`, Redis, PostgreSQL, MinIO или ComfyUI.

## Роль в DevOps-проекте

Сервис `tg-bot` закрывает пользовательский вход в систему генерации изображений.

С точки зрения DevOps-практик сервис:

* запускается в отдельном Docker-контейнере;
* конфигурируется через переменные окружения;
* не хранит секреты в коде;
* имеет зафиксированные зависимости через `uv.lock`;
* покрыт тестами API-интеграции;
* может проверяться в CI/CD pipeline.
