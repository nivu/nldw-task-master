from decimal import Decimal

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

    # Serve the interactive API docs (/docs, /redoc, /openapi.json). Off
    # unless set, so a deploy that forgets it does not publish the full route
    # map to anyone who asks; .env.example turns it on for local work.
    API_DOCS: bool = False

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

    # Spec 004 §6. The address shown to a signed-in person for connecting an
    # MCP client. Returned by the API, never baked into the web bundle. Empty
    # means the Account page explains that MCP is not enabled here.
    MCP_PUBLIC_URL: str = ""

    # Spec 006 FR-FEED. Optional dedicated secret for calendar feed keys;
    # derived from the service key when unset.
    FEED_SECRET: str = ""

    # ------------------------------------------------------------------
    # Spec 007 Gmail label sync — FR-CRM-SYNC-03, plan §2.2, Q-23
    #
    # The service-account key, raw JSON or base64 of it, is set on the
    # `worker-crm-sync` service ONLY: it is the one process that calls Gmail.
    # The API, beat and the shared worker never hold it; they read the
    # non-secret CRM_GMAIL_CONFIGURED flag instead, which says the key exists
    # there. Sync is inert while the flag is false.
    # ------------------------------------------------------------------
    GOOGLE_SERVICE_ACCOUNT_JSON: SecretStr | None = None
    CRM_GMAIL_CONFIGURED: bool = False

    # ------------------------------------------------------------------
    # Spec 008 voice capture — FR-TASK-VOICE-02, plan §2.2, T10
    #
    # The Groq key is set on the `worker` service ONLY: it is the one process
    # that transcribes. The API reads the non-secret TASKS_VOICE_CONFIGURED
    # flag instead, which says the key exists there; `POST /tasks/voice`
    # answers 503 while it is false. The key is never logged.
    # ------------------------------------------------------------------
    GROQ_API_KEY: SecretStr | None = None
    TASKS_VOICE_CONFIGURED: bool = False

    # ------------------------------------------------------------------
    # Spec 009 CRM intelligence layer — FR-CRMAI-LLM-01, -07, NFR-CRMAI-04,
    # plan §2.2
    #
    # ANTHROPIC_API_KEY (the Claude key) and FIREFLIES_API_KEY are set on the
    # `worker-crm-ai` service ONLY: it is the one process that calls Claude
    # and Fireflies. The API, beat and the other workers never hold them.
    # Never logged, never in a Celery argument.
    #
    # The rate table (USD per million input and output tokens, spec §17.1)
    # is not secret: it is set on `worker-crm-ai`, which estimates each call's
    # cost, and on the API, whose GET /crm/ai reports whether it is set, once
    # the owner confirms it. While either rate is unset every AI job is
    # skipped "AI rates are not set" and GET /crm/ai reports
    # rates_configured false (D-1, plan §11.1).
    # ------------------------------------------------------------------
    ANTHROPIC_API_KEY: SecretStr | None = None
    FIREFLIES_API_KEY: SecretStr | None = None
    CRM_AI_INPUT_USD_PER_MTOK: Decimal | None = None
    CRM_AI_OUTPUT_USD_PER_MTOK: Decimal | None = None

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
