from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class BotSettings(BaseSettings):
    """Настройки Telegram-бота и подключения к server-api."""

    telegram_bot_token: str = Field(
        default="",
        validation_alias=AliasChoices("TELEGRAM_BOT_TOKEN", "TG_BOT_TOKEN"),
    )

    server_api_url: str = Field(
        default="http://server-api:8000",
        validation_alias=AliasChoices("SERVER_API_URL", "API_BASE_URL"),
    )

    generate_endpoint: str = "/api/generate"
    status_endpoint_template: str = "/api/status/{task_id}"

    request_timeout_seconds: float = 15.0
    polling_interval_seconds: float = 5.0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="",
        extra="ignore",
    )


def load_settings() -> BotSettings:
    return BotSettings()