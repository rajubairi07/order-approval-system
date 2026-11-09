"""Application configuration, loaded from environment / .env file."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/order_approval"

    # mock | openai | anthropic | google_genai | ollama
    llm_provider: str = "mock"
    llm_model: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
