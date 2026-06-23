import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command
from aiogram.types import Message
from aiohttp_socks import SocksConnector

from .api_client import ServerApiClient, ServerApiError
from .config import load_settings


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger(__name__)

settings = load_settings()

dp = Dispatcher()

api_client = ServerApiClient(
    base_url=settings.server_api_url,
    generate_endpoint=settings.generate_endpoint,
    status_endpoint_template=settings.status_endpoint_template,
    timeout_seconds=settings.request_timeout_seconds,
)


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
        "Бот отправляет запрос в server-api, а server-api уже ставит задачу "
        "в очередь Redis и передаёт её дальше в AI pipeline."
    )


@dp.message(Command("generate"))
async def handle_generate(message: Message) -> None:
    """
    Обработчик команды /generate.

    Принимает prompt от пользователя и отправляет его в server-api.
    """

    if message.text is None:
        await message.answer("Не удалось прочитать текст команды.")
        return

    raw_prompt = message.text.replace("/generate", "", 1).strip()

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

    await message.answer("⏳ Отправляю задачу генерации в server-api...")

    try:
        task = await api_client.create_generation(
            telegram_user_id=telegram_user_id,
            chat_id=chat_id,
            prompt=prompt,
            negative_prompt=negative_prompt,
        )
    except ServerApiError as error:
        logger.exception("Failed to create generation task")
        await message.answer(
            "❌ Не удалось создать задачу генерации.\n\n"
            f"Ошибка API: {error}"
        )
        return
    except Exception as error:
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


@dp.message(Command("status"))
async def handle_status(message: Message) -> None:
    """
    Обработчик команды /status.

    Получает статус задачи генерации из server-api.
    """

    if message.text is None:
        await message.answer("Не удалось прочитать текст команды.")
        return

    task_id = message.text.replace("/status", "", 1).strip()

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
                "✅ Результат готов:",
                task.result_url,
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

    await message.answer("\n".join(lines))


async def init_bot_with_fallback(token: str, proxies_str: str) -> Bot:
    """Инициализирует Bot с использованием первого рабочего прокси из списка."""
    proxies = [p.strip() for p in proxies_str.split(",") if p.strip()]

    if not proxies:
        logger.info("No telegram proxies configured. Using direct connection.")
        return Bot(token=token)

    for proxy in proxies:
        logger.info("Trying to connect to Telegram using proxy: %s", proxy)
        try:
            connector = SocksConnector.from_url(proxy)
            session = AiohttpSession(connector=connector)
            bot = Bot(token=token, session=session)
            me = await asyncio.wait_for(bot.get_me(), timeout=10.0)
            logger.info("Successfully connected using proxy %s (Bot: @%s)", proxy, me.username)
            return bot
        except Exception as e:
            logger.warning("Connection failed via proxy %s: %s", proxy, e)

    logger.error("All proxies failed. Falling back to direct connection.")
    return Bot(token=token)


async def main() -> None:
    """Точка входа в Telegram-бота."""

    if not settings.telegram_bot_token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is not set. "
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