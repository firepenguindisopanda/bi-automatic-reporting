from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


# Models, chosen 2026-10-10 by evals.models across all 16 BI eval cases
# (evals/results/models-2026-10-10.md): every stage on one candidate, the critic
# fixed on Nemotron 3 Super with its full page window (critic/2), re-runs off so
# a weak first answer could not hide.
#
#   model                       ok     quality  median s/stage, incl. critic
#   google/gemma-4-31b-it       96/96  85.5     63
#   nvidia/nemotron-3-super     96/96  82.3     32
#   moonshotai/kimi-k3          96/96  79.1     67
#   nvidia/nemotron-3-ultra     95/96  77.3     87
#   openai/gpt-oss-20b          84/96  68.3     44   <- the previous workhorse
#
# Gemma 4 31B led every stage but one, where it tied Nemotron 3 Super - which
# is also the critic, so any self-preference favoured the runner-up. gpt-oss-20b
# returned unparseable JSON on all three attempts for four marketing stages and
# two competitive and SWOT stages, each a failed job in production, and gave
# Riomed a SWOT with only strengths. Not shortlisted: DeepSeek V4.1 Flash
# answered every call with a 504; GLM 5.3 took 50-215 s a stage and ran out of
# tokens mid-JSON. Of the 80 models GET /v1/models listed, 11 general chat
# models answered at all.
#
# The critic stays on Nemotron 3 Super: fast, 96/96 as an analyst itself, and
# from a different family than the analysts, so no stage is graded by its own
# model.
#
# Earlier history, kept because the lessons still hold:
#
# NVIDIA retired meta/llama-3.1-8b-instruct and the 49B behind competitive
# analysis at 2026-08-26T09:00:00Z, so both models this app used stopped
# answering mid-run.
#
# To find replacements, ask the API rather than the client library.
# `available_models` is compiled into langchain_nvidia_ai_endpoints and still
# lists both retired models; GET /v1/models on integrate.api.nvidia.com does not.
# It is a filter, not a promise - of its 83 entries only 14 actually answer a
# chat completion, the rest returning 404 - so the probe still has to run.
#
# Both roles were then settled by driving candidates through this app's own JSON
# path (build_json_messages -> parse_json_response) on the real profile stage,
# and scoring the output with the critic. Completeness cannot rank models: it
# read 100.0 for all eight candidates, which is the pinned metric M1 exists to
# expose. Confidence discriminated - 60 to 95 across the same three cases.
#
#   gpt-oss-20b   7.4s, critic mean 95.0 across stripe/ghost/govuk
#   nemotron-3    120B MoE. A reasoning model, which is why the critic needs
#                 MAX_CRITIC_TOKENS headroom to reach its JSON at all.
#
# WORKHORSE_MODEL was minimaxai/minimax-m3 for part of 2026-08-26. It is neither
# retired nor unlisted: it throttles. It was the only 429 in a sweep of the whole
# catalogue, sustained over 90 minutes while 13 other models answered normally.
# The 429 carries no Retry-After and no rate-limit headers, NVIDIA does not
# publish per-model limits, and the documented remedy is client-side failover.
# Since this constant backs five of six BI stages, one throttled model stops
# every job while DEEP_MODEL sits idle.
#
# Caveat worth keeping in view: prompts.py is tuned to the retired model, and
# every eval score from before this date was measured on equipment that no
# longer exists.
WORKHORSE_MODEL = "google/gemma-4-31b-it"
DEEP_MODEL = "nvidia/nemotron-3-super-120b-a12b"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    nvidia_api_key: str = ""
    nvidia_model: str = WORKHORSE_MODEL
    temperature: float = 0.0
    max_tokens: int = 4096
    llm_timeout: int = 120

    agent_model_business_profile: str = WORKHORSE_MODEL
    agent_model_market_analysis: str = WORKHORSE_MODEL
    # Was the one stage on the deeper model. Gemma tied it here (75.9 against
    # 76.4, judged by the deeper model itself), so it joins the rest.
    agent_model_competitive_analysis: str = WORKHORSE_MODEL
    agent_model_swot: str = WORKHORSE_MODEL
    agent_model_marketing: str = WORKHORSE_MODEL
    agent_model_report_writer: str = WORKHORSE_MODEL
    agent_model_market_research: str = WORKHORSE_MODEL
    # Where a stage goes when its own model cannot answer: broken JSON on every
    # repair attempt, or a throttled, overloaded or retired model. The runner-up
    # in the 2026-10-10 sweep, and the fastest. Empty turns failover off.
    agent_fallback_model: str = DEEP_MODEL

    # Stage quality signal
    enable_stage_critic: bool = True
    # M2 made the critic load-bearing: it now arbitrates which retry is kept,
    # so judging with the cheapest model in the stack is no longer free.
    # Also the direct test of SPEC assumption 3 - whether the 89-clustering
    # was the 8B echoing its own rubric back.
    agent_model_critic: str = DEEP_MODEL
    critic_retry_threshold: float = 60.0
    agent_max_attempts: int = 3

    # Job runner. Concurrency stays at 1 on small hosts: rendering is the
    # memory ceiling and two concurrent renders push a small container over it.
    job_concurrency: int = 1
    job_queue_max: int = 20
    # A failed or interrupted job can be resumed from its first unfinished
    # stage. Bounded, because each retry spends model calls.
    max_retries_per_job: int = 2

    # Finished jobs, their stage rows and their report files are deleted after
    # this many days without an update. 0 (the default) keeps everything:
    # deletion is opted into, never something an upgrade starts doing.
    retention_days: int = 0
    retention_sweep_hours: int = 6

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
    # Behind a reverse proxy every request appears to come from the proxy,
    # which would make the rate limit global instead of per-client. Only enable
    # this when a trusted reverse proxy is the sole ingress.
    trust_proxy_headers: bool = True

    cors_origins: str = "http://localhost:5173,http://localhost:4173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    # Branding, shared by the email, the PDF cover and the sender name. The
    # author lines are left out of both when empty.
    brand_name: str = "Rubric"
    brand_tagline: str = "Business intelligence, read closely."
    brand_author: str = "Nicholas Smith"
    brand_author_email: str = "nicosmith.smith3@gmail.com"
    brand_author_url: str = "https://nicksmith.is-a.dev/"
    # Where the web app is reachable, e.g. https://host/reports. Gives the email
    # a link to the job page; empty leaves the link out.
    public_base_url: str = ""

    # Email. SMTP is the default because it works with any mail vendor, so
    # switching provider is a config change rather than a code change.
    email_provider: str = "smtp"
    from_email: str = "reports@bisystem.com"
    # Display name on the From line. Empty uses brand_name.
    from_name: str = ""
    email_max_attempts: int = 3
    email_max_attachment_mb: int = 20

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_security: str = "starttls"
    smtp_timeout: int = 30

    # Outbound mail abuse limits. Nothing authenticates the submitter, and the
    # submitter names the recipient, so without these the service relays mail
    # to any address on demand.
    #
    # recipient_allowlist: addresses and/or domains, separated by commas or semicolons
    # ("me@example.com, @example.org"). Empty allows any recipient.
    recipient_allowlist: str = ""
    max_reports_per_recipient_per_day: int = 5
    max_deliveries_per_job: int = 3
    # The people who run this deployment: always allowed as recipients, and exempt
    # from both caps above, so testing is not rationed. Same format as the allowlist.
    # The rate limit on submit/resend/retry still applies to them.
    owner_emails: str = ""

    sendgrid_api_key: str = ""
    resend_api_key: str = ""
    report_output_dir: str = "reports"
    jobs_db: str = "jobs.db"


settings = Settings()
