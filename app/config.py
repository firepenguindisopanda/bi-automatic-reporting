from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    nvidia_api_key: str = ""
    nvidia_model: str = "meta/llama-3.1-8b-instruct"
    temperature: float = 0.0
    max_tokens: int = 4096
    llm_timeout: int = 120

    agent_model_business_profile: str = "meta/llama-3.1-8b-instruct"
    agent_model_market_analysis: str = "meta/llama-3.1-8b-instruct"
    agent_model_competitive_analysis: str = "nvidia/llama-3.3-nemotron-super-49b-v1.5"
    agent_model_swot: str = "meta/llama-3.1-8b-instruct"
    agent_model_marketing: str = "meta/llama-3.1-8b-instruct"
    agent_model_report_writer: str = "meta/llama-3.1-8b-instruct"
    agent_model_market_research: str = "meta/llama-3.1-8b-instruct"

    # Stage quality signal
    enable_stage_critic: bool = True
    agent_model_critic: str = "meta/llama-3.1-8b-instruct"
    critic_retry_threshold: float = 60.0
    agent_max_attempts: int = 3

    # Job runner. Concurrency stays at 1 on the Pi: rendering is the memory
    # ceiling and two concurrent renders push the container over it.
    job_concurrency: int = 1
    job_queue_max: int = 20

    app_title: str = "Business Intelligence System"
    app_version: str = "0.2.0"

    database_url: str = ""
    upstash_redis_rest_token: str = ""
    upstash_redis_rest_url: str = ""

    # Outbound fetch policy. Disabling the guard lets the scraper reach hosts on
    # the deployment's own network - only do that on an isolated machine.
    scraper_block_private_networks: bool = True
    scraper_max_redirects: int = 5

    rate_limit_max_requests: int = 10
    rate_limit_window: int = 60
    # Behind nginx every request appears to come from 127.0.0.1, which would
    # make the rate limit global instead of per-client. Only enable this when a
    # trusted reverse proxy is the sole ingress.
    trust_proxy_headers: bool = True

    cors_origins: str = "http://localhost:5173,http://localhost:4173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    # Email. SMTP is the default because it works with any mail vendor, so
    # switching provider is a config change rather than a code change.
    email_provider: str = "smtp"
    from_email: str = "reports@bisystem.com"
    email_max_attempts: int = 3
    email_max_attachment_mb: int = 20

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_security: str = "starttls"
    smtp_timeout: int = 30

    sendgrid_api_key: str = ""
    resend_api_key: str = ""
    report_output_dir: str = "reports"
    jobs_db: str = "jobs.db"


settings = Settings()
