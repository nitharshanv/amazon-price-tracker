# Lightweight Amazon & Flipkart Price Tracker Bot

A high-performance, database-free Python Telegram Bot designed specifically for low-resource single-board computers like the **Raspberry Pi Zero 2 W (512MB RAM)**.

---

## ⚡ Features & Optimizations for Raspberry Pi Zero 2 W

- **No Heavy Database Engine**: Runs entirely without PostgreSQL, MySQL, Redis, or Docker. Everything is stored in an atomic, schema-validated JSON file.
- **Ultra-Low Memory Footprint**: Operates in ~25–40 MB of RAM using `asyncio` and `slots=True` dataclasses.
- **SD Card Protection**:
  - **In-Memory Caching**: Avoids repetitive SD card reads.
  - **Dirty Checking**: Only writes to the SD card when prices or tracking change.
  - **Atomic POSIX Writes**: Writes to a temporary file, calls `os.fsync`, and atomically replaces the file to prevent corruption during power cuts.
  - **Daily Rolling Backups**: Automatically maintains up to 5 rolling daily backups in `data/backup/`.
- **Deduplication Engine**: If 100 users track the same product, the bot executes only **1 network request** and notifies all eligible users.
- **Controlled Concurrency**: Limits concurrent network requests (`MAX_CONCURRENT_REQUESTS=2`) with small intervals to eliminate CPU spikes on 1GHz ARM cores.
- **Resilient Fallback**: Supports the Amazon Creators API as well as an automatic, lightweight HTTP parser fallback so it works immediately out of the box without requiring affiliate API credentials.

---

## 📁 Project Structure

```text
amazon-price-tracker/
├── bot.py                  # Main Telegram bot application & conversation handlers
├── config.py               # Environment configuration & validation
├── storage.py              # Atomic JSON storage engine with in-memory cache & backups
├── requirements.txt        # Minimal Python dependencies
├── .env.example            # Environment variables template
├── .gitignore              # Git ignore rules
│
├── providers/              # E-commerce platform providers
│   ├── __init__.py         # Provider registry & resolver
│   ├── base.py             # ProductInfo dataclass & abstract provider interface
│   ├── amazon.py           # Amazon.in provider (Creators API + scraper fallback)
│   └── flipkart.py         # Flipkart provider (PID/ITM extraction + scraper)
│
├── services/               # Core business logic
│   ├── __init__.py
│   ├── tracker.py          # User tracking, list, remove, history services
│   └── scheduler.py        # Deduplicated periodic price checker & alerter
│
├── utils/                  # Helper utilities
│   ├── __init__.py
│   ├── url_parser.py       # URL regex extraction & normalization
│   └── formatter.py        # Currency (₹ Indian numbering) & string formatters
│
├── tests/                  # Automated unit test suite
│   ├── test_storage.py     # Storage atomicity & concurrency tests
│   ├── test_providers.py   # URL & ASIN/PID parsing tests
│   └── test_services.py    # Deduplication & anti-spam alert tests
│
└── data/                   # Persistent runtime data (created automatically)
    ├── storage.json        # Main database file
    └── backup/             # Daily rolling backups
```

---

## 🛠️ Raspberry Pi 2W Quick Setup

### 1. Enable Swap on Raspberry Pi (Recommended)
Because the Pi Zero 2 W has 512MB RAM, having a 512MB or 1GB swap prevents out-of-memory errors during dependency installation:
```bash
sudo dphys-swapfile swapoff
sudo sed -i 's/CONF_SWAPSIZE=.*/CONF_SWAPSIZE=1024/' /etc/dphys-swapfile
sudo dphys-swapfile setup
sudo dphys-swapfile swapon
```

### 2. Clone and Setup Virtual Environment
```bash
cd "/mnt/sda/amazon price tracker"

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Configure Environment Variables
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
nano .env
```

Configure:
- `TELEGRAM_BOT_TOKEN`: Your bot token obtained from [@BotFather](https://t.me/BotFather).
- `CHECK_INTERVAL_MINUTES`: Frequency of background price checks (default `30` minutes).
- `MAX_CONCURRENT_REQUESTS`: Keep at `2` for Pi 2W.
- `MAX_HISTORY_ENTRIES`: Keep at `20` to prevent unbounded memory and disk growth.

---

## 🚀 Running the Bot

### Manual Execution (Testing)
```bash
source venv/bin/activate
python bot.py
```

### Run as a Background Service with Systemd (Production)
To ensure the bot starts automatically on boot and recovers from crashes:

1. Create a service file:
```bash
sudo nano /etc/systemd/system/price-tracker.service
```

2. Paste the following configuration (adjust paths and user if needed):
```ini
[Unit]
Description=Amazon & Flipkart Price Tracker Telegram Bot
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/mnt/sda/amazon price tracker
ExecStart=/mnt/sda/amazon price tracker/venv/bin/python bot.py
Restart=always
RestartSec=10
EnvironmentFile=/mnt/sda/amazon price tracker/.env

# Low memory optimizations for Pi 2W
MemoryMax=150M
Nice=10

[Install]
WantedBy=multi-user.target
```

3. Enable and start the service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable price-tracker
sudo systemctl start price-tracker
```

4. Check logs:
```bash
sudo journalctl -u price-tracker -f
```

---

## 🤖 Telegram Bot Commands & Usage

Register these commands with `@BotFather` using `/setcommands`:
```text
start - Start the bot & welcome guide
list - View your tracked products
remove - Stop tracking a product
history - View price history of a product
check - Manually check current prices now
help - Show help and usage guide
```

### User Workflow
1. **Send Link**:
   User sends `https://www.amazon.in/dp/B09XXXXXXX` or Flipkart link.
2. **Current Price**:
   Bot fetches the product title and current selling price.
3. **Set Alert**:
   Bot prompts: *"What price should I alert you at? Example: 25000"*.
4. **Tracking Activated**:
   User enters target price, and the bot monitors the product on every scheduled interval.
5. **Alert**:
   When price $\le$ target, the bot immediately sends an alert with a direct **🛒 Open Product** button.
