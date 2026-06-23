from dataclasses import dataclass
from typing import Any

import httpx


class ServerApiError(RuntimeError):
    """Ошибка обмена с server-api."""


@dataclass(slots=True)
class GenerationTask:
    """Данные задачи генерации, которые bot получает от server-api."""

    task_id: str
    status: str
    position: int | None = None
    result_url: str | None = None
    error: str | None = None


class ServerApiClient:
    """HTTP-клиент для обмена Telegram-бота с server-api."""

    def __init__(
        self,
        base_url: str,
        generate_endpoint: str = "/api/generate",
        status_endpoint_template: str = "/api/status/{task_id}",
        timeout_seconds: float = 15.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.generate_endpoint = generate_endpoint
        self.status_endpoint_template = status_endpoint_template
        self.timeout_seconds = timeout_seconds

    async def create_generation(
        self,
        telegram_user_id: int,
        chat_id: int,
        prompt: str,
        negative_prompt: str | None = None,
    ) -> GenerationTask:
        """Создать задачу генерации изображения."""

        payload: dict[str, Any] = {
            "telegram_user_id": telegram_user_id,
            "chat_id": chat_id,
            "prompt": prompt,
        }

        if negative_prompt:
            payload["negative_prompt"] = negative_prompt

        data = await self._post_json(self.generate_endpoint, payload)
        return self._parse_generation_task(data)

    async def get_generation_status(self, task_id: str) -> GenerationTask:
        """Получить статус задачи генерации."""

        endpoint = self.status_endpoint_template.format(task_id=task_id)
        data = await self._get_json(endpoint)
        return self._parse_generation_task(data, fallback_task_id=task_id)

    async def _post_json(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = self._build_url(endpoint)

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as exc:
            raise ServerApiError(
                f"server-api returned HTTP {exc.response.status_code}: {exc.response.text}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ServerApiError(f"server-api request failed: {exc}") from exc

    async def _get_json(self, endpoint: str) -> dict[str, Any]:
        url = self._build_url(endpoint)

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.get(url)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as exc:
            raise ServerApiError(
                f"server-api returned HTTP {exc.response.status_code}: {exc.response.text}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ServerApiError(f"server-api request failed: {exc}") from exc

    def _build_url(self, endpoint: str) -> str:
        if not endpoint.startswith("/"):
            endpoint = f"/{endpoint}"

        return f"{self.base_url}{endpoint}"

    @staticmethod
    def _parse_generation_task(
        data: dict[str, Any],
        fallback_task_id: str | None = None,
    ) -> GenerationTask:
        task_id = (
            data.get("task_id")
            or data.get("job_id")
            or data.get("id")
            or fallback_task_id
        )

        if task_id is None:
            raise ServerApiError(f"server-api response does not contain task id: {data}")

        position = data.get("position") or data.get("queue_position")

        return GenerationTask(
            task_id=str(task_id),
            status=str(data.get("status", "unknown")),
            position=int(position) if position is not None else None,
            result_url=data.get("result_url") or data.get("url"),
            error=data.get("error"),
        )