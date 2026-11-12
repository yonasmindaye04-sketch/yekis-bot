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
    auth_allow_anonymous_dev: bool = False
    cors_origins: str = ""

    # Telegram collector (M1). Disabled by default; credentials come from the
    # environment only and are never committed or logged.
    telegram_enabled: bool = False
    telegram_api_id: int | None = None
    telegram_api_hash: str | None = None
    # Optional: bot credentials used for PUBLISHING only. Telegram bots cannot
    # read channel history, so collection always uses a user session.
    telegram_bot_token: str | None = None
    # User session used by the collector (phone + login code + optional 2FA).
    telegram_session_path: str = "var/telegram_user"
    # Bot session used by the publisher when no bot token is configured.
    telegram_bot_session_path: str = "var/telegram_bot"
    telegram_fetch_limit: int = 100
    # Optional proxy for Telegram traffic. Use it to route MTProto through a
    # Cloudflare tunnel or a Cloudflare WARP SOCKS5 proxy, which is the
    # supported way to reach Telegram from a restricted network.
    # Example (WARP proxy mode): socks5 / 127.0.0.1 / 40000
    telegram_proxy_type: str = ""
    telegram_proxy_host: str = ""
    telegram_proxy_port: int = 0
    telegram_proxy_rdns: bool = True

    # Background processing (M2).
    redis_url: str = "redis://localhost:6382/0"
    celery_task_always_eager: bool = False
    celery_task_max_retries: int = 5
    celery_retry_base_seconds: int = 10
    celery_retry_max_seconds: int = 3600
    celery_task_time_limit_seconds: int = 300
    collect_interval_seconds: int = 900

    # Extraction (M3). "rules" is the safe default; "llm" (OpenAI-compatible)
    # and "gemini" (Google AI Studio) are opt-in.
    extraction_provider: str = "rules"
    llm_api_key: str | None = None
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    gemini_api_key: str | None = None
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    gemini_model: str = "gemini-2.5-flash"
    llm_timeout_seconds: float = 30.0
    # Provider-level retry for transient failures (timeout, 429, 5xx). NVIDIA
    # NIM cold starts can exceed a single request timeout, so one bounded
    # retry is enabled by default. Schema/credential failures are not retried.
    llm_max_attempts: int = 2
    llm_retry_wait_seconds: float = 5.0
    llm_max_input_chars: int = 12000
    llm_max_output_tokens: int = 800
    # "json_object" asks the provider for JSON mode. Some OpenAI-compatible
    # endpoints (some NVIDIA NIM model deployments) reject it; set to "none"
    # to omit the parameter.
    llm_response_format: str = "json_object"
    # Optional provider-specific request fields as a JSON object, for example
    # NVIDIA NIM guided decoding: {"guided_json": {...}}. Merged into the
    # request body without replacing the standard fields.
    llm_extra_body: str = ""

    # Publishing (M5). Never enabled by default.
    publishing_enabled: bool = False
    publishing_allowed_channels: str = ""
    publishing_max_attempts: int = 3

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def telegram_ready(self) -> bool:
        return bool(self.telegram_enabled and self.telegram_api_id and self.telegram_api_hash)

    @property
    def telegram_proxy(self) -> tuple[str, str, int, bool] | None:
        """Telethon proxy tuple, or None when no proxy is configured."""
        kind = self.telegram_proxy_type.strip().lower()
        if not kind or not self.telegram_proxy_host or not self.telegram_proxy_port:
            return None
        if kind not in ("socks5", "http"):
            raise ValueError(
                "TELEGRAM_PROXY_TYPE must be 'socks5' or 'http', "
                f"got {kind!r}"
            )
        return (
            kind,
            self.telegram_proxy_host,
            int(self.telegram_proxy_port),
            self.telegram_proxy_rdns,
        )

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


def get_settings() -> Settings:
    return settings
