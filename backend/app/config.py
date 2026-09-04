from pydantic import SecretStr
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    Only infrastructure and credentials belong here. Anything a person might
    reasonably want to change without a deploy — the carry-forward policy, the
    lock behaviour, allowance figures — lives in the `app_settings` table
    instead (FR-BAL-02: allowances MUST NOT be hardcoded).
    """

    SUPABASE_URL: str
    SUPABASE_SERVICE_ROLE_KEY: SecretStr
    REDIS_URL: str = "redis://localhost:6379/0"
    FRONTEND_URL: str = "http://localhost:3000"

    # Run a Celery worker inside the API process.
    #
    # Production should deploy a dedicated `worker` service, so this must stay
    # off there — otherwise two pools consume the same queue and the API
    # container carries a worker's memory footprint alongside uvicorn.
    #
    # Useful locally, where it saves running a second process by hand.
    # Set RUN_EMBEDDED_WORKER=true to opt in.
    RUN_EMBEDDED_WORKER: bool = False

    # ------------------------------------------------------------------
    # Notifications — FR-NOTIF
    #
    # Every sender is inert until its credentials are present. A missing
    # credential is logged and skipped; it never fails a booking (FR-NOTIF-05).
    # ------------------------------------------------------------------
    SLACK_BOT_TOKEN: SecretStr | None = None
    SLACK_SIGNING_SECRET: SecretStr | None = None

    SMTP_HOST: str | None = None
    SMTP_PORT: int = 587
    SMTP_USER: str | None = None
    SMTP_PASSWORD: SecretStr | None = None
    SMTP_FROM: str = "nunnari-portal@nunnari.example"

    # How long a verified access token is trusted before GoTrue is asked again.
    #
    # Supabase now signs tokens with rotating asymmetric keys (ES256), so the
    # backend verifies by asking the auth server rather than by holding a
    # shared secret. That is a network hop per request, which this cache makes
    # negligible. Kept short so a deactivated user (FR-AUTH-06) stops being
    # able to act within seconds rather than minutes.
    AUTH_CACHE_SECONDS: int = 30

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    @property
    def slack_enabled(self) -> bool:
        return bool(self.SLACK_BOT_TOKEN and self.SLACK_BOT_TOKEN.get_secret_value())

    @property
    def email_enabled(self) -> bool:
        return bool(self.SMTP_HOST)


settings = Settings()
