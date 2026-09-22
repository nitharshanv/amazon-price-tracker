"""Configuration module for Amazon & Flipkart Price Tracker Bot.

Optimized for low-spec devices like Raspberry Pi Zero 2 W (512MB RAM).
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file from project root if present
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# Telegram Configuration
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CONNECT_TIMEOUT: float = float(os.getenv("TELEGRAM_CONNECT_TIMEOUT", "30.0"))
TELEGRAM_READ_TIMEOUT: float = float(os.getenv("TELEGRAM_READ_TIMEOUT", "30.0"))
TELEGRAM_WRITE_TIMEOUT: float = float(os.getenv("TELEGRAM_WRITE_TIMEOUT", "30.0"))
TELEGRAM_BOOTSTRAP_RETRIES: int = int(os.getenv("TELEGRAM_BOOTSTRAP_RETRIES", "5"))
TELEGRAM_PROXY: str = os.getenv("TELEGRAM_PROXY", "").strip()
TELEGRAM_BASE_URL: str = os.getenv("TELEGRAM_BASE_URL", "").strip()

# Scheduler settings
CHECK_INTERVAL_MINUTES: int = int(os.getenv("CHECK_INTERVAL_MINUTES", "30"))

# Storage settings
STORAGE_FILE: Path = BASE_DIR / os.getenv("STORAGE_FILE", "data/storage.json")
BACKUP_DIR: Path = STORAGE_FILE.parent / "backup"
MAX_BACKUPS: int = 5
MAX_HISTORY_ENTRIES: int = int(os.getenv("MAX_HISTORY_ENTRIES", "20"))

# Performance & network concurrency (tuned for Pi 2W)
MAX_CONCURRENT_REQUESTS: int = int(os.getenv("MAX_CONCURRENT_REQUESTS", "2"))
REQUEST_TIMEOUT: float = float(os.getenv("REQUEST_TIMEOUT", "15.0"))

# Optional Amazon Creators API
AMAZON_CLIENT_ID: str = os.getenv("AMAZON_CLIENT_ID", "").strip()
AMAZON_CLIENT_SECRET: str = os.getenv("AMAZON_CLIENT_SECRET", "").strip()
AMAZON_REFRESH_TOKEN: str = os.getenv("AMAZON_REFRESH_TOKEN", "").strip()

# User Agent for web requests
DEFAULT_USER_AGENT: str = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def validate_config() -> None:
    """Validate required configuration at startup."""
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "your_telegram_bot_token_here":
        raise ValueError(
            "TELEGRAM_BOT_TOKEN is not set or is still the placeholder 'your_telegram_bot_token_here'!\n"
            "Please open your .env file and replace it with your real token from @BotFather on Telegram."
        )
    if ":" not in TELEGRAM_BOT_TOKEN:
        raise ValueError(
            f"TELEGRAM_BOT_TOKEN '{TELEGRAM_BOT_TOKEN}' does not appear to be a valid Telegram bot token!\n"
            "A valid token typically looks like '1234567890:ABC-DEF1234ghIkl-zyx57W2v1u123ew11'.\n"
            "Please obtain a valid token from @BotFather on Telegram."
        )
