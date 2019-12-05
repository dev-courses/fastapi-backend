import pytest
from fastapi import FastAPI
from httpx import Client
from starlette.status import HTTP_422_UNPROCESSABLE_ENTITY

pytestmark = pytest.mark.asyncio


async def test_frw_validation_error_format(app: FastAPI):
    @app.get("/wrong_path/{param}")
    def route_for_test(param: int) -> None:  # pragma: no cover
        pass

    client = Client(base_url="http://testserver", app=app)
    response = await client.get("/wrong_path/asd")
    assert response.status_code == HTTP_422_UNPROCESSABLE_ENTITY

    error_data = response.json()
    assert "errors" in error_data
