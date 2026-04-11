from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    anthropic_api_key: str

    # Whisper (local, via faster-whisper)
    whisper_model: str = "base"  # tiny | base | small | medium | large-v3

    # Storage
    storage_backend: str = "local"  # "local" or "s3"
    data_dir: str = "./data"

    # S3 (optional)
    s3_bucket: str = ""
    aws_region: str = "us-east-1"

    # Embeddings
    embedding_model: str = "all-MiniLM-L6-v2"

    # Alchemist OS — creative writer (Opus, adaptive thinking)
    generation_model: str = "claude-opus-4-6"

    # Omi webhook — secret key required on all webhook requests
    webhook_secret: str = ""

    # Omi webhook — ignore transcripts shorter than this word count (filters noise)
    omi_min_words: int = 10
    # Wake word — transcript must contain this (case-insensitive) to be processed.
    # Set to empty string to process everything (not recommended for always-on use).
    omi_wake_word: str = "hey man"

    # Nano Claw — cognitive agent (Haiku + tools)
    nano_claw_model: str = "claude-haiku-4-5-20251001"
    nano_claw_user_id: str = "default"  # primary user for Telegram and vault

    # Obsidian vault path on Pi (e.g. /app/vault in Docker)
    obsidian_vault_path: str = ""

    # Telegram bot (leave empty to disable)
    telegram_bot_token: str = ""
    # Chat ID to post daily digest to (get from @userinfobot or /status command)
    telegram_chat_id: str = ""

    # Email / SMTP (leave empty to disable sending)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""  # defaults to smtp_user if blank

    # Email / IMAP (leave empty to disable reading)
    # Defaults to SMTP_HOST if not set separately
    imap_host: str = ""
    imap_port: int = 993

    # ── Multi-LLM providers ───────────────────────────────────────────────────

    # Google Gemini — best for long-context summarization (YouTube transcripts, etc.)
    gemini_api_key: str = ""

    # Ollama — local model server (http://ollama:11434 if using Docker compose)
    ollama_base_url: str = ""
    ollama_model: str = "llama3.2"

    # ── Calendar integration ──────────────────────────────────────────────────

    # CalDAV (read + write events — Nextcloud, Radicale, etc.)
    caldav_url: str = ""
    caldav_user: str = ""
    caldav_password: str = ""

    # iCal URL (read-only — Google Calendar "Secret address in iCal format")
    ical_url: str = ""

    # ── GitHub polling ────────────────────────────────────────────────────────
    # Optional — 60 req/h without token, 5000 req/h with
    github_token: str = ""

    # ── Daily digest ──────────────────────────────────────────────────────────
    # Base URL for digest links in Telegram messages (e.g. http://192.168.0.27:8000)
    digest_base_url: str = ""


settings = Settings()
