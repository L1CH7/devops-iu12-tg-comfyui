import httpx
import pytest
import respx

from src.api_client import ServerApiClient, ServerApiError


@pytest.mark.asyncio
async def test_create_generation_returns_task_data() -> None:
    """API-клиент должен создать задачу генерации и распарсить ответ server-api."""

    client = ServerApiClient(base_url="http://server-api:8000")

    with respx.mock:
        respx.post("http://server-api:8000/api/generate").mock(
            return_value=httpx.Response(
                status_code=200,
                json={
                    "task_id": "task-123",
                    "status": "pending",
                    "position": 3,
                },
            )
        )

        task = await client.create_generation(
            telegram_user_id=1001,
            chat_id=2002,
            prompt="cat astronaut on Mars",
            negative_prompt="blur, low quality",
        )

    assert task.task_id == "task-123"
    assert task.status == "pending"
    assert task.position == 3
    assert task.result_url is None
    assert task.error is None


@pytest.mark.asyncio
async def test_get_generation_status_returns_result_url() -> None:
    """API-клиент должен получить статус задачи и ссылку на готовый результат."""

    client = ServerApiClient(base_url="http://server-api:8000")

    with respx.mock:
        respx.get("http://server-api:8000/api/status/task-123").mock(
            return_value=httpx.Response(
                status_code=200,
                json={
                    "task_id": "task-123",
                    "status": "completed",
                    "result_url": "http://minio:9000/generations/task-123.png",
                },
            )
        )

        task = await client.get_generation_status("task-123")

    assert task.task_id == "task-123"
    assert task.status == "completed"
    assert task.result_url == "http://minio:9000/generations/task-123.png"


@pytest.mark.asyncio
async def test_api_client_accepts_alternative_task_id_fields() -> None:
    """
    API-клиент должен быть устойчив к разным названиям ID задачи.

    Это полезно, пока backend-контракт ещё может меняться:
    task_id / job_id / id.
    """

    client = ServerApiClient(base_url="http://server-api:8000")

    with respx.mock:
        respx.post("http://server-api:8000/api/generate").mock(
            return_value=httpx.Response(
                status_code=200,
                json={
                    "job_id": "job-777",
                    "status": "queued",
                    "queue_position": 1,
                },
            )
        )

        task = await client.create_generation(
            telegram_user_id=1001,
            chat_id=2002,
            prompt="test prompt",
        )

    assert task.task_id == "job-777"
    assert task.status == "queued"
    assert task.position == 1


@pytest.mark.asyncio
async def test_create_generation_raises_error_on_server_failure() -> None:
    """При ошибке server-api клиент должен выбрасывать ServerApiError."""

    client = ServerApiClient(base_url="http://server-api:8000")

    with respx.mock:
        respx.post("http://server-api:8000/api/generate").mock(
            return_value=httpx.Response(
                status_code=500,
                text="internal server error",
            )
        )

        with pytest.raises(ServerApiError):
            await client.create_generation(
                telegram_user_id=1001,
                chat_id=2002,
                prompt="test prompt",
            )