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

    app_title: str = "Business Intelligence System"
    app_version: str = "0.2.0"

    database_url: str = ""
    upstash_redis_rest_token: str = ""
    upstash_redis_rest_url: str = ""

    email_provider: str = "smtp"
    sendgrid_api_key: str = ""
    from_email: str = "reports@bisystem.com"
    report_output_dir: str = "reports"
    jobs_db: str = "jobs.db"


settings = Settings()
