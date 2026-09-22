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
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError(
            "TELEGRAM_BOT_TOKEN is not set in environment or .env file! "
            "Please obtain a token from @BotFather and set it."
        )
