from __future__ import annotations

from functools import lru_cache

from pydantic import AliasChoices, Field

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
except ImportError:
    import os

    from pydantic import BaseModel, ConfigDict

    class BaseSettings(BaseModel):  # type: ignore[no-redef]
        def __init__(self, **data):
            env_data = {}
            for field_name in [
                "gemini_api_key",
                "gemini_model",
                "llm_daily_limit",
                "byok_daily_limit",
                "analyze_per_minute",
                "llm_timeout_s",
                "max_agent_iterations",
                "app_version",
            ]:
                env_val = os.getenv(field_name.upper())
                if env_val is not None:
                    env_data[field_name] = env_val
            env_data.update(data)
            super().__init__(**env_data)

    SettingsConfigDict = ConfigDict  # type: ignore[misc, assignment]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    gemini_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    )
    gemini_model: str = Field(
        default="gemini-flash-latest",
        validation_alias=AliasChoices("GEMINI_MODEL"),
    )
    llm_daily_limit: int = Field(
        default=5,
        validation_alias=AliasChoices("LLM_DAILY_LIMIT"),
    )
    byok_daily_limit: int = Field(
        default=50,
        validation_alias=AliasChoices("BYOK_DAILY_LIMIT"),
    )
    analyze_per_minute: int = Field(
        default=60,
        validation_alias=AliasChoices("ANALYZE_PER_MINUTE"),
    )
    llm_timeout_s: float = Field(
        default=25.0,
        validation_alias=AliasChoices("LLM_TIMEOUT_S"),
    )
    max_agent_iterations: int = Field(
        default=3,
        validation_alias=AliasChoices("MAX_AGENT_ITERATIONS"),
    )
    app_version: str = Field(
        default="1.0.0",
        validation_alias=AliasChoices("APP_VERSION"),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
