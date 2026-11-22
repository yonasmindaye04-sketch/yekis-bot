from pydantic import field_validator, model_validator
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
    # Posts published in the channel more than this many days ago are not
    # collected at all. The channels carry years of history, and an advert
    # from last year is not an opportunity anybody can act on. Set to 0 to
    # keep every post regardless of its age.
    collection_max_post_age_days: int = 30
    # Optional proxy for Telegram traffic. Use it to route MTProto through a
    # Cloudflare tunnel or a Cloudflare WARP SOCKS5 proxy, which is the
    # supported way to reach Telegram from a restricted network.
    # Example (WARP proxy mode): socks5 / 127.0.0.1 / 40000
    telegram_proxy_type: str = ""
    telegram_proxy_host: str = ""
    telegram_proxy_port: int = 0
    telegram_proxy_rdns: bool = True

    @field_validator("telegram_proxy_port", mode="before")
    @classmethod
    def _blank_port_is_zero(cls, value):
        """An empty value means "no port"; it must not break startup."""
        if value is None or (isinstance(value, str) and not value.strip()):
            return 0
        return value

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
    # Request timeout for a single model call. The default is deliberately
    # above the 30 seconds that a hosted API usually needs: the self-hosted
    # NVIDIA NIM endpoint used in production answers in 90-180 seconds, and a
    # too short timeout silently degrades the pipeline to the deterministic
    # rules, which can never reach the automatic approval threshold.
    llm_timeout_seconds: float = 120.0
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
    platform_url: str = "https://yekis.com"

    # Classification engine (Stage 3 of the classification pipeline).
    # "rules" means deterministic only: no model call and therefore no post
    # can reach the automatic approval threshold. "llm" is any
    # OpenAI-compatible endpoint (including NVIDIA NIM) and "gemini" is the
    # Google AI Studio transport. The AI stage only ever proposes a
    # classification; it never lowers a deterministic risk verdict.
    classification_provider: str = "rules"
    # Optional model override for the classification stage. Empty means the
    # provider default (LLM_MODEL / GEMINI_MODEL).
    classification_model: str = ""

    # Autonomous approval and publishing. Disabled by default and defaulting
    # to shadow mode: nothing is published without an explicit owner action.
    autonomy_enabled: bool = False
    autonomy_mode: str = "shadow"
    autonomy_auto_approval_threshold: float = 0.95
    # Comma-separated source ids that may be published autonomously.
    autonomy_approved_source_ids: str = ""
    # Comma-separated job categories that may be published autonomously.
    autonomy_approved_categories: str = ""
    autonomy_daily_publish_limit: int = 5
    autonomy_min_interval_seconds: int = 900
    # Channel used in supervised mode (pilot) and in production mode.
    autonomy_pilot_channel: str = ""
    autonomy_target_channel: str = ""
    # Beat cadence for the autonomous pipeline.
    autonomy_evaluate_interval_seconds: int = 300
    autonomous_publish_interval_seconds: int = 120
    # Maximum number of opportunities the scheduled job classifies in one run.
    # A model call can take a minute or more, so the batch has to stay small
    # enough to finish inside the job timeout. The scheduled runs are sparse
    # (GitHub delays cron entries by hours), so every run has to catch up on
    # a decent batch.
    autonomy_evaluate_limit: int = 12
    # How long an opportunity waits before the cycle looks at it again. The
    # first attempt classifies it; a later attempt re-tries a post that could
    # not be classified because the model was unavailable.
    autonomy_evaluation_retry_seconds: int = 21600
    # How long an opportunity stays actionable. An older post is closed
    # automatically instead of waiting for a human forever.
    autonomy_stale_after_hours: int = 720
    # Wall clock budget for the evaluation phase of a scheduled run.
    autonomy_evaluation_time_budget_seconds: int = 2100

    # Wall clock budget for the collection phase of a scheduled run. Each
    # ingested post can cost one model call, so without a budget a backlog of
    # new posts can push the run past the job timeout and kill it before the
    # publishing phase is ever reached. What is left over is picked up by the
    # next run.
    collection_time_budget_seconds: int = 900

    # Admin Alerts
    admin_alerts_enabled: bool = False
    admin_alert_chat_id: str = ""
    admin_alert_batch_size: int = 10

    # GitHub Actions Integration (optional on-demand trigger)
    github_token: str = ""
    github_repository: str = "yonasmindaye04-sketch/yekis-bot"
    github_workflow_id: str = "collector.yml"

    # Marketplace Sync & Webhook
    marketplace_sync_enabled: bool = False
    marketplace_webhook_url: str = ""
    marketplace_webhook_secret: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def _ignore_blank_environment_values(cls, values):
        """Treat an empty environment variable as "not set".

        A secret that exists but is empty is common in scheduled jobs. Passing
        an empty string to a boolean or an integer field would crash the
        process at import time, so the blank value is dropped and the field
        keeps its default.
        """
        if not isinstance(values, dict):
            return values
        cleaned = {}
        for key, value in values.items():
            if isinstance(value, str) and value.strip() == "":
                continue
            cleaned[key] = value
        return cleaned

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

    @property
    def autonomy_source_id_list(self) -> list[int]:
        """Source ids that are approved for autonomous publishing."""
        result: list[int] = []
        for value in self.autonomy_approved_source_ids.split(","):
            value = value.strip()
            if not value:
                continue
            try:
                result.append(int(value))
            except ValueError:
                continue
        return result

    @property
    def autonomy_category_list(self) -> list[str]:
        """Job categories that are approved for autonomous publishing."""
        return [
            value.strip().lower()
            for value in self.autonomy_approved_categories.split(",")
            if value.strip()
        ]


settings = Settings()  # type: ignore[call-arg]


def get_settings() -> Settings:
    return settings
