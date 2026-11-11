from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from TARS_-prefixed environment variables."""

    model_config = SettingsConfigDict(env_prefix="TARS_", env_file=".env")

    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-live"
    database_url: str = "sqlite:///./tars.db"
    outlet_match_threshold: float = 0.85
    sku_match_threshold: float = 0.80


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()

