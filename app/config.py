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

    app_title: str = "Business Intelligence System"
    app_version: str = "0.2.0"

    email_provider: str = "smtp"
    sendgrid_api_key: str = ""
    from_email: str = "reports@bisystem.com"
    report_output_dir: str = "reports"
    jobs_db: str = "jobs.db"


settings = Settings()
