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

## 🤖 Telegram Bot Commands & Interactive Usage

Register these commands with `@BotFather` using `/setcommands`:
```text
start - Welcome guide & quick navigation buttons
list - View tracked products with interactive action buttons
check - Manually check current prices now with telemetry stats
history - View price fluctuation trail and min/max stats
remove - Stop tracking a product
help - Show help and usage guide
```

### 📱 Enhanced User Workflow (Obsidian Telemetry UX)

1. **Send Link**:
   User sends any Amazon.in (`https://www.amazon.in/dp/B0BDK62PDX`) or Flipkart product link.
2. **Rich Product Card**:
   The bot extracts the title, verified platform tag (`Amazon.in Verified`), current selling price, and original MRP with discount percentage (`₹19,999 <s>₹24,900</s> (-20%)`).
3. **One-Tap Target Presets**:
   Instead of forcing manual typing, the bot presents interactive preset buttons:
   `[ -5% (₹18,999) ]` `[ -10% (₹17,999) ]` `[ -15% (₹16,999) ]` `[ ❌ Cancel ]`
   *(User can tap any preset to set the target instantly, or type a custom number).*
4. **Tracker Registered**:
   The bot confirms registration with cadence details (`Every 8h via Cron`), next poll estimate, and an inline `[ 🛒 Open Product ]` button.
5. **Interactive Watchlist (`/list`)**:
   Shows all tracked items with real-time status badges (`🎯 Target Met!` vs `⏳ Active`). Every product features dedicated buttons:
   `[ 🛒 Open ]` `[ 📊 History ]` `[ ❌ Del ]` plus `[ 🔄 Check Prices Now ]`.
6. **Price Fluctuation Trail (`/history`)**:
   Renders a chronological telemetry price trail:
   - Recent check points with delta indicators (`📉`, `📈`, `▪️`)
   - 🟢 All-Time Lowest Price recorded
   - 🔴 All-Time Highest Price recorded
   - 📊 Net Fluctuation (`-₹2,009 (-10.1% across 48h)`)
7. **Instant Price Check (`/check`)**:
   Runs gentle sequential fetches via `Semaphore(2)`, returning price comparisons (`<s>₹19,999</s> → ₹17,990 (⬇ -₹2,009)`) and runtime telemetry (`Checked in 0.84s • Memory Safe`).
8. **Crash Alert Notification**:
   When price drops below target, sends a priority alert card with:
   - `📉 PRICE CRASH: ₹17,990 (was ₹19,999)`
   - `💰 SAVED: -₹2,009`
   - Quick action buttons: `[ 🛒 Open Product ]` `[ 📊 Price History ]` `[ ❌ Stop Tracking ]`.
