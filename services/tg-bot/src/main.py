import asyncio
import logging

import httpx
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, Message

from .api_client import GenerationTask, ServerApiClient, ServerApiError
from .config import load_settings


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger(__name__)

settings = load_settings()

dp = Dispatcher()

# Множество для отслеживания активных генераций пользователей
active_generations = set()

api_client = ServerApiClient(
    base_url=settings.server_api_url,
    generate_endpoint=settings.generate_endpoint,
    status_endpoint_template=settings.status_endpoint_template,
    timeout_seconds=settings.request_timeout_seconds,
)


def get_command_argument(text: str, command: str) -> str:
    """
    Достаёт аргумент после команды.

    Поддерживает оба варианта:
    /generate дом
    /generate@bot_username дом
    """

    parts = text.strip().split(maxsplit=1)

    if not parts:
        return ""

    command_part = parts[0]

    if not command_part.startswith(f"/{command}"):
        return ""

    if len(parts) == 1:
        return ""

    return parts[1].strip()


def split_prompt_and_negative(text: str) -> tuple[str, str | None]:
    """
    Разбирает пользовательский ввод.

    Поддерживаем простой формат:
    /generate красивый лес на закате --negative blur, low quality

    Всё до --negative считается основным prompt.
    Всё после --negative считается negative_prompt.
    """

    marker = "--negative"

    if marker not in text:
        return text.strip(), None

    prompt, negative_prompt = text.split(marker, maxsplit=1)

    prompt = prompt.strip()
    negative_prompt = negative_prompt.strip()

    return prompt, negative_prompt or None


def format_task_response(task_id: str, status: str, position: int | None = None) -> str:
    """Формирует понятный ответ пользователю после создания задачи."""

    lines = [
        "✅ Задача принята в очередь генерации.",
        "",
        f"ID задачи: {task_id}",
        f"Статус: {status}",
    ]

    if position is not None:
        lines.append(f"Позиция в очереди: {position}")

    lines.extend(
        [
            "",
            "Проверить статус можно командой:",
            f"/status {task_id}",
        ]
    )

    return "\n".join(lines)


def format_status_response(task: GenerationTask) -> str:
    """Формирует текстовый ответ по статусу задачи."""

    lines = [
        f"ID задачи: {task.task_id}",
        f"Статус: {task.status}",
    ]

    if task.position is not None:
        lines.append(f"Позиция в очереди: {task.position}")

    if task.result_url:
        lines.extend(
            [
                "",
                "✅ Результат готов.",
                f"URL результата: {task.result_url}",
            ]
        )

    if task.error:
        lines.extend(
            [
                "",
                "Ошибка генерации:",
                task.error,
            ]
        )

    return "\n".join(lines)


async def send_generated_image(bot: Bot, chat_id: int, task: GenerationTask) -> None:
    """
    Скачивает результат генерации и отправляет пользователю картинку.

    result_url может быть внутренним Docker-адресом, например:
    http://comfyui:8188/view?...
    или
    http://minio:9000/...

    Пользователь снаружи такой URL может не открыть, зато контейнер tg-bot
    внутри Docker-сети может скачать файл и отправить его в Telegram.
    """

    if not task.result_url:
        await bot.send_message(
            chat_id=chat_id,
            text=format_status_response(task),
        )
        return

    try:
        result_download_timeout = getattr(
            settings,
            "result_download_timeout_seconds",
            30.0,
        )

        async with httpx.AsyncClient(timeout=result_download_timeout) as client:
            response = await client.get(task.result_url)
            response.raise_for_status()
            image_bytes = response.content

        image = BufferedInputFile(
            image_bytes,
            filename=f"{task.task_id}.png",
        )

        await bot.send_photo(
            chat_id=chat_id,
            photo=image,
            caption=f"✅ Генерация готова\nID задачи: {task.task_id}",
        )

    except Exception as error:
        logger.exception("Failed to download or send generated image")

        await bot.send_message(
            chat_id=chat_id,
            text=(
                "✅ Генерация завершена, но бот не смог скачать картинку "
                "и отправить её файлом.\n\n"
                f"ID задачи: {task.task_id}\n"
                f"URL результата: {task.result_url}\n\n"
                f"Ошибка скачивания: {error}"
            ),
        )


async def wait_for_generation_result(bot: Bot, chat_id: int, task_id: str, telegram_user_id: int) -> None:
    """
    После /generate бот сам периодически проверяет статус задачи.

    Если server-api получил callback от n8n и обновил задачу до completed,
    бот скачивает картинку и отправляет её пользователю.
    """
    try:
        polling_interval = max(1.0, float(settings.polling_interval_seconds))
        wait_timeout = float(getattr(settings, "generation_wait_timeout_seconds", 300.0))
        max_attempts = max(1, int(wait_timeout / polling_interval))

        logger.info(
            "Start polling generation result: task_id=%s, interval=%s, max_attempts=%s",
            task_id,
            polling_interval,
            max_attempts,
        )

        for attempt in range(1, max_attempts + 1):
            await asyncio.sleep(polling_interval)

            try:
                task = await api_client.get_generation_status(task_id)
            except Exception as error:
                logger.warning(
                    "Failed to poll generation status: task_id=%s, attempt=%s, error=%s",
                    task_id,
                    attempt,
                    error,
                )
                continue

            status = task.status.lower()

            logger.info(
                "Generation status polled: task_id=%s, status=%s, attempt=%s",
                task_id,
                status,
                attempt,
            )

            if status in {"completed", "done", "success"}:
                await send_generated_image(
                    bot=bot,
                    chat_id=chat_id,
                    task=task,
                )
                return

            if status in {"failed", "error"}:
                await bot.send_message(
                    chat_id=chat_id,
                    text=(
                        "❌ Генерация завершилась ошибкой.\n\n"
                        f"ID задачи: {task.task_id}\n"
                        f"Ошибка: {task.error or 'не указана'}"
                    ),
                )
                return

        await bot.send_message(
            chat_id=chat_id,
            text=(
                "⏳ Время ожидания генерации истекло.\n\n"
                f"ID задачи: {task_id}\n"
                "Задача могла остаться в обработке. Проверь статус вручную:\n"
                f"/status {task_id}"
            ),
        )
    finally:
        active_generations.discard(telegram_user_id)


@dp.message(Command("start"))
async def handle_start(message: Message) -> None:
    """Обработчик команды /start."""

    await message.answer(
        "Привет! Я Telegram-бот для генерации изображений через AI-инфраструктуру.\n\n"
        "Основные команды:\n"
        "/generate <описание> — создать задачу генерации\n"
        "/status <id> — проверить статус задачи\n"
        "/help — показать справку"
    )


@dp.message(Command("help"))
async def handle_help(message: Message) -> None:
    """Обработчик команды /help."""

    await message.answer(
        "Как пользоваться ботом:\n\n"
        "1. Создать задачу генерации:\n"
        "/generate кот-космонавт на Марсе\n\n"
        "2. Создать задачу с negative prompt:\n"
        "/generate cyberpunk city --negative blur, low quality\n\n"
        "3. Проверить статус задачи:\n"
        "/status <id_задачи>\n\n"
        "Бот отправляет запрос в server-api, а server-api ставит задачу "
        "в очередь Redis, передаёт её дальше в AI pipeline и ждёт callback "
        "от n8n/ComfyUI."
    )


@dp.message(Command("generate"))
async def handle_generate(message: Message, bot: Bot) -> None:
    """
    Обработчик команды /generate.

    Принимает prompt от пользователя, отправляет его в server-api,
    а затем запускает фоновое ожидание результата.
    """

    if message.text is None:
        await message.answer("Не удалось прочитать текст команды.")
        return

    raw_prompt = get_command_argument(message.text, "generate")

    if not raw_prompt:
        await message.answer(
            "После команды нужно указать описание картинки.\n\n"
            "Пример:\n"
            "/generate кот-космонавт на Марсе"
        )
        return

    prompt, negative_prompt = split_prompt_and_negative(raw_prompt)

    if len(prompt) < 3:
        await message.answer("Описание слишком короткое. Напиши prompt подробнее.")
        return

    telegram_user_id = message.from_user.id if message.from_user else 0
    chat_id = message.chat.id

    if telegram_user_id != 0 and telegram_user_id in active_generations:
        await message.answer(
            "❌ У вас уже есть активная задача генерации. "
            "Пожалуйста, дождитесь завершения текущей генерации, прежде чем отправлять новый запрос."
        )
        return

    active_generations.add(telegram_user_id)

    await message.answer("⏳ Отправляю задачу генерации в server-api...")

    try:
        task = await api_client.create_generation(
            telegram_user_id=telegram_user_id,
            chat_id=chat_id,
            prompt=prompt,
            negative_prompt=negative_prompt,
        )
    except ServerApiError as error:
        active_generations.discard(telegram_user_id)
        logger.exception("Failed to create generation task")
        await message.answer(
            "❌ Не удалось создать задачу генерации.\n\n"
            f"Ошибка API: {error}"
        )
        return
    except Exception as error:
        active_generations.discard(telegram_user_id)
        logger.exception("Unexpected error while creating generation task")
        await message.answer(
            "❌ Произошла непредвиденная ошибка при создании задачи.\n\n"
            f"Ошибка: {error}"
        )
        return

    await message.answer(
        format_task_response(
            task_id=task.task_id,
            status=task.status,
            position=task.position,
        )
    )

    asyncio.create_task(
        wait_for_generation_result(
            bot=bot,
            chat_id=chat_id,
            task_id=task.task_id,
            telegram_user_id=telegram_user_id,
        )
    )


@dp.message(Command("status"))
async def handle_status(message: Message, bot: Bot) -> None:
    """
    Обработчик команды /status.

    Получает статус задачи генерации из server-api.
    Если задача completed и есть result_url — отправляет картинку.
    """

    if message.text is None:
        await message.answer("Не удалось прочитать текст команды.")
        return

    task_id = get_command_argument(message.text, "status")

    if not task_id:
        await message.answer(
            "Укажи ID задачи.\n\n"
            "Пример:\n"
            "/status task-123"
        )
        return

    try:
        task = await api_client.get_generation_status(task_id)
    except ServerApiError as error:
        logger.exception("Failed to get generation status")
        await message.answer(
            "❌ Не удалось получить статус задачи.\n\n"
            f"Ошибка API: {error}"
        )
        return
    except Exception as error:
        logger.exception("Unexpected error while getting generation status")
        await message.answer(
            "❌ Произошла непредвиденная ошибка при проверке статуса.\n\n"
            f"Ошибка: {error}"
        )
        return

    status = task.status.lower()

    if status in {"completed", "done", "success"} and task.result_url:
        await send_generated_image(
            bot=bot,
            chat_id=message.chat.id,
            task=task,
        )
        return

    await message.answer(format_status_response(task))


async def init_bot_with_fallback(token: str, proxies_str: str) -> Bot:
    """Инициализирует Bot с использованием первого рабочего прокси из списка."""
    proxies = [p.strip() for p in proxies_str.split(",") if p.strip()]

    if not proxies:
        logger.info("No telegram proxies configured. Using direct connection.")
        return Bot(token=token)

    for proxy in proxies:
        logger.info("Trying to connect to Telegram using proxy: %s", proxy.split("@")[-1])
        try:
            if proxy.startswith("vless://"):
                raise ValueError("vless proxy scheme is not supported")

            session = AiohttpSession(proxy=proxy)
            bot = Bot(token=token, session=session)
            try:
                me = await asyncio.wait_for(bot.get_me(), timeout=10.0)
            except Exception:
                await bot.session.close()
                raise
            logger.info("Successfully connected using proxy %s (Bot: @%s)", proxy, me.username)
            return bot
        except Exception as e:
            logger.warning("Connection failed via proxy %s: %s", proxy.split("@")[-1], e)

    logger.error("All proxies failed. Falling back to direct connection.")
    return Bot(token=token)


async def main() -> None:
    """Точка входа в Telegram-бота."""

    if not settings.telegram_bot_token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN or TG_BOT_TOKEN is not set. "
            "Set it in .env or Docker Compose environment."
        )

    logger.info("Starting Telegram bot")
    logger.info("Server API URL: %s", settings.server_api_url)

    bot = await init_bot_with_fallback(settings.telegram_bot_token, settings.telegram_proxies)
    
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())