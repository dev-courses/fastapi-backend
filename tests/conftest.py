import uuid
import warnings
from os import environ, getenv

import alembic.config
import docker as libdocker
import pytest
from asgi_lifespan import LifespanManager
from asyncpg import Connection
from asyncpg.pool import Pool
from asyncpg.transaction import Transaction
from fastapi import FastAPI
from httpx import Client

from app.db.repositories.articles import ArticlesRepository
from app.db.repositories.users import UsersRepository
from app.models.domain.articles import Article
from app.models.domain.users import UserInDB
from app.services import jwt
from tests.testing_helpers import FakePool, ping_postgres, pull_image

POSTGRES_DOCKER_IMAGE = "postgres:11.4-alpine"

environ["MAX_CONNECTIONS_COUNT"] = "1"
environ["MIN_CONNECTIONS_COUNT"] = "1"
environ["SECRET_KEY"] = "secret"

USE_LOCAL_DB = getenv("USE_LOCAL_DB_FOR_TEST", False)


@pytest.fixture(scope="session")
def docker() -> libdocker.APIClient:
    return libdocker.APIClient(version="auto")


@pytest.fixture(scope="session", autouse=True)
def postgres_server(docker: libdocker.APIClient) -> None:
    warnings.filterwarnings("ignore", category=DeprecationWarning)

    if not USE_LOCAL_DB:  # pragma: no cover
        pull_image(docker, POSTGRES_DOCKER_IMAGE)

        container = docker.create_container(
            image=POSTGRES_DOCKER_IMAGE,
            name="test-postgres-{}".format(uuid.uuid4()),
            detach=True,
        )
        docker.start(container=container["Id"])
        inspection = docker.inspect_container(container["Id"])
        host = inspection["NetworkSettings"]["IPAddress"]

        dsn = f"postgres://postgres:postgres@{host}/postgres"

        try:
            ping_postgres(dsn)
            environ["DB_CONNECTION"] = dsn

            alembic.config.main(argv=["upgrade", "head"])

            yield container

            alembic.config.main(argv=["downgrade", "base"])
        finally:
            docker.kill(container["Id"])
            docker.remove_container(container["Id"])
    else:  # pragma: no cover
        yield
        return


@pytest.fixture
def app() -> FastAPI:
    from app.main import get_application  # local import for testing purpose

    return get_application()


@pytest.fixture
def pool(app: FastAPI) -> Pool:
    return app.state.pool


# here starts db transaction that is required for almost all tests
@pytest.fixture(autouse=True)
async def client(app: FastAPI) -> Client:
    async with LifespanManager(app):
        app.state.pool = await FakePool.create_pool(app.state.pool)
        connection: Connection
        async with app.state.pool.acquire() as connection:
            transaction: Transaction = connection.transaction()
            await transaction.start()
            async with Client(
                app=app,
                base_url="http://testserver",
                headers={"Content-Type": "application/json"},
            ) as client:
                yield client
            await transaction.rollback()
        await app.state.pool.close()


@pytest.fixture
def authorization_prefix() -> str:
    from app.core.config import JWT_TOKEN_PREFIX

    return JWT_TOKEN_PREFIX


@pytest.fixture
async def test_user(pool: Pool) -> UserInDB:
    async with pool.acquire() as conn:
        return await UsersRepository(conn).create_user(
            email="test@test.com", password="password", username="username"
        )


@pytest.fixture
async def test_article(test_user: UserInDB, pool: Pool) -> Article:
    async with pool.acquire() as connection:
        articles_repo = ArticlesRepository(connection)
        return await articles_repo.create_article(
            slug="test-slug",
            title="Test Slug",
            description="Slug for tests",
            body="Test " * 100,
            author=test_user,
            tags=["tests", "testing", "pytest"],
        )


@pytest.fixture
def token(test_user: UserInDB) -> str:
    return jwt.create_access_token_for_user(test_user, environ["SECRET_KEY"])


@pytest.fixture
def authorized_client(client: Client, token: str, authorization_prefix: str) -> Client:
    client.headers = {
        "Authorization": f"{authorization_prefix} {token}",
        **client.headers,
    }
    return client
