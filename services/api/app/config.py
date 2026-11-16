"""Settings, read from the environment (docker compose locally, Secrets Manager-backed env on ECS)."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", case_sensitive=False)

    database_url: str  # postgresql+asyncpg://user:pass@host:5432/db
    aws_region: str = "us-east-1"

    # "cognito" verifies Cognito JWTs. "dev" accepts `Bearer dev:<email>[:admin]` tokens and must never be set
    # outside local development; the app refuses to start with it when ENVIRONMENT is not "local".
    auth_mode: Literal["cognito", "dev"] = "cognito"
    environment: Literal["local", "test", "staging", "production"] = "production"
    cognito_user_pool_id: str = ""
    cognito_app_client_id: str = ""
    admin_group: str = "platform_admin"

    cors_origins: list[str] = []


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if settings.auth_mode == "dev" and settings.environment not in ("local", "test"):
        raise RuntimeError("AUTH_MODE=dev is only allowed when ENVIRONMENT is local or test")
    if settings.auth_mode == "cognito" and not (settings.cognito_user_pool_id and settings.cognito_app_client_id):
        raise RuntimeError("AUTH_MODE=cognito needs COGNITO_USER_POOL_ID and COGNITO_APP_CLIENT_ID")
    return settings
