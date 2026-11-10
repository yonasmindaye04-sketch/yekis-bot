from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Yekis Intelligence"
    app_version: str = "0.3.0"
    app_env: str = "development"
    api_prefix: str = "/api/v1"

    # Required: there is deliberately no credential-bearing default. A missing
    # DATABASE_URL must fail loudly instead of silently using a dev password.
    database_url: str

    auto_create_schema: bool = True
    log_level: str = "INFO"

    # Service-to-service authentication (M4). JSON object:
    #   {"<api-key>": {"role": "reviewer", "name": "Ops reviewer"}}
    auth_api_keys: str = ""
    cors_origins: str = ""
    rate_limit_per_minute: int = 120

    # Telegram collector (M1). Disabled by default; credentials come from the
    # environment only and are never committed or logged.
    telegram_enabled: bool = False
    telegram_api_id: int | None = None
    telegram_api_hash: str | None = None
    telegram_session_path: str = "var/telegram_collector"
    telegram_fetch_limit: int = 100

    # Background processing (M2).
    redis_url: str = "redis://localhost:6382/0"
    celery_task_always_eager: bool = False
    celery_task_max_retries: int = 5
    celery_retry_base_seconds: int = 10
    celery_retry_max_seconds: int = 3600
    celery_task_time_limit_seconds: int = 300
    collect_interval_seconds: int = 900

    # Extraction (M3). "rules" is the safe default; "llm" is opt-in.
    extraction_provider: str = "rules"
    llm_api_key: str | None = None
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: float = 30.0
    llm_max_input_chars: int = 12000
    llm_max_output_tokens: int = 800
    llm_max_skills: int = 20

    # Publishing (M5). Never enabled by default.
    publishing_enabled: bool = False
    publishing_channel: str = ""
    publishing_allowed_channels: str = ""
    publishing_max_attempts: int = 3

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def telegram_ready(self) -> bool:
        return bool(self.telegram_enabled and self.telegram_api_id and self.telegram_api_hash)

    @property
    def allowed_publish_channels(self) -> list[str]:
        return [
            value.strip()
            for value in self.publishing_allowed_channels.split(",")
            if value.strip()
        ]

    @property
    def cors_origin_list(self) -> list[str]:
        return [value.strip() for value in self.cors_origins.split(",") if value.strip()]


settings = Settings()  # type: ignore[call-arg]
