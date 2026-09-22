"""Main Telegram Bot application for Amazon & Flipkart price tracking.

Optimized for Raspberry Pi Zero 2 W (single async process, low RAM footprint).
"""

import asyncio
import logging
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from config import (
    CHECK_INTERVAL_MINUTES,
    TELEGRAM_BASE_URL,
    TELEGRAM_BOOTSTRAP_RETRIES,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CONNECT_TIMEOUT,
    TELEGRAM_PROXY,
    TELEGRAM_READ_TIMEOUT,
    TELEGRAM_WRITE_TIMEOUT,
    validate_config,
)
from providers import get_provider
from providers.base import ProductInfo
from services.scheduler import PriceCheckerScheduler
from services.tracker import TrackerService
from storage import StorageManager
from utils.formatter import clean_title, format_currency, parse_price_text
from utils.url_parser import extract_urls

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Conversation States
WAITING_FOR_TARGET_PRICE = 1

# Service singletons
storage = StorageManager.get_instance()
tracker = TrackerService(storage)
scheduler_service = PriceCheckerScheduler(storage=storage)


# --- Command Handlers ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /start command."""
    if not update.effective_user or not update.message:
        return ConversationHandler.END

    await storage.register_user(update.effective_user.id)
    welcome_text = (
        "👋 <b>Welcome to Price Tracker Bot!</b>\n\n"
        "I can track prices for products on <b>Amazon India</b> and <b>Flipkart</b> "
        "and alert you the moment they drop to your target price.\n\n"
        "<b>How to use:</b>\n"
        "1. Send me any Amazon or Flipkart product link.\n"
        "2. Reply with your desired target price.\n"
        "3. Sit back and relax! I'll ping you when the price drops.\n\n"
        "<b>Commands:</b>\n"
        "/list - View your tracked products\n"
        "/remove &lt;number&gt; - Stop tracking an item\n"
        "/history &lt;number&gt; - View price history\n"
        "/check - Check current prices now\n"
        "/help - Show help guide"
    )
    await update.message.reply_text(welcome_text, parse_mode="HTML")
    return ConversationHandler.END


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /help command."""
    if not update.message:
        return ConversationHandler.END

    help_text = (
        "📖 <b>Price Tracker Bot Commands</b>\n\n"
        "• <b>Send a Link:</b> Paste any Amazon.in or Flipkart URL to start tracking.\n"
        "• <b>/list:</b> View all products currently being tracked.\n"
        "• <b>/remove &lt;number&gt;:</b> Remove a product by its list index (e.g. <code>/remove 1</code>).\n"
        "• <b>/history &lt;number&gt;:</b> View recent price history for a product.\n"
        "• <b>/check:</b> Trigger an instant price check on your tracked items.\n"
        "• <b>/cancel:</b> Cancel an active product addition.\n\n"
        "💡 <i>Tip: Checks run automatically every "
        f"{CHECK_INTERVAL_MINUTES} minutes.</i>"
    )
    await update.message.reply_text(help_text, parse_mode="HTML")
    return ConversationHandler.END


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /list command to display tracked products."""
    if not update.effective_user or not update.message:
        return ConversationHandler.END

    items = await tracker.get_user_items(update.effective_user.id)
    if not items:
        await update.message.reply_text(
            "📦 <b>You are not tracking any products yet.</b>\n\n"
            "Send me an Amazon or Flipkart link to start tracking!",
            parse_mode="HTML",
        )
        return ConversationHandler.END

    lines = ["📦 <b>Your tracked products:</b>\n"]
    for idx, item in enumerate(items, 1):
        prod = item.get("product", {})
        title = prod.get("title", "Product")
        platform = prod.get("platform", "Unknown").capitalize()
        curr_price = format_currency(prod.get("price"))
        target_price = format_currency(item.get("target_price"))

        lines.append(
            f"<b>{idx}. {title}</b>\n"
            f"   🏪 {platform}\n"
            f"   💰 {curr_price} → 🎯 {target_price}\n"
        )

    lines.append("Use <code>/remove &lt;number&gt;</code> to stop tracking.")
    lines.append("Use <code>/history &lt;number&gt;</code> to view price history.")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)
    return ConversationHandler.END


async def remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /remove <number> command."""
    if not update.effective_user or not update.message:
        return ConversationHandler.END

    if not context.args:
        await update.message.reply_text(
            "Please specify which item to remove from your list.\n"
            "Example: <code>/remove 1</code>\n\n"
            "Use <code>/list</code> to see item numbers.",
            parse_mode="HTML",
        )
        return ConversationHandler.END

    identifier = context.args[0].strip()
    success, msg = await tracker.remove_user_item(update.effective_user.id, identifier)
    await update.message.reply_text(f"{'✅' if success else '❌'} {msg}")
    return ConversationHandler.END


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /history <number> command."""
    if not update.effective_user or not update.message:
        return ConversationHandler.END

    if not context.args:
        await update.message.reply_text(
            "Please specify which item you want price history for.\n"
            "Example: <code>/history 1</code>\n\n"
            "Use <code>/list</code> to see item numbers.",
            parse_mode="HTML",
        )
        return ConversationHandler.END

    identifier = context.args[0].strip()
    success, msg, data = await tracker.get_item_history(update.effective_user.id, identifier)

    if not success or not data:
        await update.message.reply_text(f"❌ {msg}")
        return ConversationHandler.END

    prod = data.get("product", {})
    title = prod.get("title", "Product")
    history_points = data.get("history", [])

    lines = [f"📊 <b>{title}</b>\n"]

    if history_points:
        lines.append(f"<b>Recent price checks ({len(history_points)}):</b>\n")
        prices = []
        for pt in history_points[-10:]:  # Show last 10 points
            p = pt.get("price")
            prices.append(p)
            lines.append(f"• {format_currency(p)}")

        if len(prices) >= 2:
            change = prices[-1] - prices[0]
            sign = "+" if change > 0 else ""
            lines.append(f"\n📉 <b>Net Change:</b> {sign}{format_currency(change)}")
    else:
        curr = prod.get("price")
        lines.append(f"Current price: {format_currency(curr)}")
        lines.append("<i>No additional history recorded yet.</i>")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    return ConversationHandler.END


async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /check command to immediately refresh prices for this user."""
    if not update.effective_user or not update.message:
        return ConversationHandler.END

    status_msg = await update.message.reply_text("🔄 Checking current prices for your tracked items...")

    results = await scheduler_service.check_user_items_now(update.effective_user.id)
    if not results:
        await status_msg.edit_text(
            "📦 You have no tracked items to check.\n"
            "Send an Amazon or Flipkart link to start tracking!"
        )
        return ConversationHandler.END

    lines = ["🔍 <b>Price check results:</b>\n"]
    for idx, res in enumerate(results, 1):
        title = res.get("title", "Product")
        old_p = res.get("old_price")
        new_p = res.get("new_price")
        target_p = res.get("target_price")

        if old_p and old_p != new_p:
            diff = new_p - old_p
            diff_str = f" ({'+' if diff > 0 else ''}{format_currency(diff)})"
            price_text = f"{format_currency(old_p)} → {format_currency(new_p)}{diff_str}"
        else:
            price_text = f"{format_currency(new_p)}"

        lines.append(
            f"<b>{idx}. {title}</b>\n"
            f"   💰 Current: {price_text} | 🎯 Target: {format_currency(target_p)}\n"
        )

    await status_msg.edit_text("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)
    return ConversationHandler.END


# --- URL & Target Price Conversation Flow ---

async def handle_url_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Extract URL, fetch product details, and prompt for target price."""
    if not update.message or not update.message.text:
        return ConversationHandler.END

    text = update.message.text.strip()
    urls = extract_urls(text)
    if not urls:
        # User sent non-URL plain text outside active conversation
        await update.message.reply_text(
            "Please send a valid <b>Amazon.in</b> or <b>Flipkart</b> link.\n\n"
            "Example: <code>https://www.amazon.in/dp/B09XXXXXXX</code>",
            parse_mode="HTML",
        )
        return ConversationHandler.END

    target_url = urls[0]
    provider = get_provider(target_url)
    if not provider:
        await update.message.reply_text(
            "❌ <b>Unsupported link.</b>\n"
            "Currently only Amazon India (<code>amazon.in</code>) and Flipkart links are supported.",
            parse_mode="HTML",
        )
        return ConversationHandler.END

    fetch_msg = await update.message.reply_text("🔍 Fetching product details...")

    try:
        product_info = await provider.get_product(target_url)
    except Exception as e:
        logger.error("Error fetching product: %s", e)
        product_info = None

    if not product_info:
        await fetch_msg.edit_text(
            "⚠️ <b>Could not retrieve product details.</b>\n"
            "Please check that the link is an active product page and try again.",
            parse_mode="HTML",
        )
        return ConversationHandler.END

    # Save pending product in context for this user
    context.user_data["pending_product"] = product_info

    prompt_text = (
        f"🎧 <b>{product_info.title}</b>\n\n"
        f"💰 <b>Current price:</b> {format_currency(product_info.price, product_info.currency)}\n\n"
        f"What price should I alert you at?\n\n"
        f"<i>Example:</i>\n"
        f"<code>{int(product_info.price * 0.9)}</code>\n\n"
        f"<i>(Send /cancel to abort)</i>"
    )
    await fetch_msg.edit_text(prompt_text, parse_mode="HTML")
    return WAITING_FOR_TARGET_PRICE


async def handle_target_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive target price from user, save tracking, and confirm."""
    if not update.effective_user or not update.message or not update.message.text:
        return ConversationHandler.END

    text = update.message.text.strip()
    target_price = parse_price_text(text)

    product_info: Optional[ProductInfo] = context.user_data.get("pending_product")
    if not product_info:
        await update.message.reply_text(
            "⚠️ Session expired. Please send the product link again."
        )
        return ConversationHandler.END

    if target_price is None or target_price <= 0:
        await update.message.reply_text(
            "❌ Please enter a valid positive number for your target price.\n"
            "Example: <code>25000</code>\n"
            "Or send /cancel to abort.",
            parse_mode="HTML",
        )
        return WAITING_FOR_TARGET_PRICE

    # Save tracking
    success, msg, _ = await tracker.add_product_tracking(
        user_id=update.effective_user.id,
        url=product_info.url,
        target_price=target_price,
        prefetched_product=product_info,
    )

    # Clear pending data
    context.user_data.pop("pending_product", None)

    if success:
        confirm_text = (
            "✅ <b>Tracking started!</b>\n\n"
            f"🎧 <b>{product_info.title}</b>\n\n"
            f"Current: {format_currency(product_info.price, product_info.currency)}\n"
            f"Target: {format_currency(target_price, product_info.currency)}\n\n"
            f"<i>I'll notify you when the price reaches your target.</i>"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛒 Open Product", url=product_info.url)]
        ])
        await update.message.reply_text(confirm_text, parse_mode="HTML", reply_markup=keyboard)
    else:
        await update.message.reply_text(f"❌ {msg}")

    return ConversationHandler.END


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel active conversation."""
    context.user_data.pop("pending_product", None)
    if update.message:
        await update.message.reply_text("Action cancelled.")
    return ConversationHandler.END


# --- Scheduled Job Callback ---

async def scheduled_check_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback for repeating scheduler job."""
    logger.info("Executing scheduled price check cycle...")
    try:
        await scheduler_service.check_all_prices()
    except Exception as e:
        logger.error("Error in scheduled check job: %s", e)


# --- Application Bootstrap ---

async def post_init(application: Application) -> None:
    """Initialize storage and scheduler after bot startup."""
    await storage.initialize()
    scheduler_service.set_bot(application.bot)
    logger.info("Storage initialized and scheduler bot instance attached.")


def build_application() -> Application:
    """Build and configure the Telegram application."""
    validate_config()

    builder = (
        ApplicationBuilder()
        .token(TELEGRAM_BOT_TOKEN)
        .connect_timeout(TELEGRAM_CONNECT_TIMEOUT)
        .read_timeout(TELEGRAM_READ_TIMEOUT)
        .write_timeout(TELEGRAM_WRITE_TIMEOUT)
        .pool_timeout(TELEGRAM_CONNECT_TIMEOUT)
        .get_updates_connect_timeout(TELEGRAM_CONNECT_TIMEOUT)
        .get_updates_read_timeout(TELEGRAM_READ_TIMEOUT)
        .get_updates_write_timeout(TELEGRAM_WRITE_TIMEOUT)
        .connection_pool_size(8)
        .http_version("1.1")
        .post_init(post_init)
    )

    if TELEGRAM_PROXY:
        logger.info("Using Telegram proxy: %s", TELEGRAM_PROXY)
        builder = builder.proxy(TELEGRAM_PROXY).get_updates_proxy(TELEGRAM_PROXY)

    if TELEGRAM_BASE_URL and TELEGRAM_BASE_URL.rstrip("/") != "https://api.telegram.org/bot":
        logger.info("Using custom Telegram API base URL: %s", TELEGRAM_BASE_URL)
        builder = builder.base_url(TELEGRAM_BASE_URL)

    application = builder.build()

    # Main Conversation Handler (URL -> Target Price)
    conv_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.TEXT & (~filters.COMMAND), handle_url_message),
        ],
        states={
            WAITING_FOR_TARGET_PRICE: [
                MessageHandler(filters.TEXT & (~filters.COMMAND), handle_target_price),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_command),
            CommandHandler("start", start_command),
            CommandHandler("help", help_command),
            CommandHandler("list", list_command),
            CommandHandler("remove", remove_command),
            CommandHandler("history", history_command),
            CommandHandler("check", check_command),
        ],
    )

    # Register handlers
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("list", list_command))
    application.add_handler(CommandHandler("remove", remove_command))
    application.add_handler(CommandHandler("history", history_command))
    application.add_handler(CommandHandler("check", check_command))

    # Setup Scheduler (run every CHECK_INTERVAL_MINUTES)
    if application.job_queue:
        interval_seconds = CHECK_INTERVAL_MINUTES * 60
        application.job_queue.run_repeating(
            scheduled_check_job,
            interval=interval_seconds,
            first=30,  # First check 30 seconds after bot boot
        )
        logger.info("Scheduled repeating price check job every %d minutes", CHECK_INTERVAL_MINUTES)
    else:
        logger.warning(
            "JobQueue is not enabled in python-telegram-bot! "
            "Install 'python-telegram-bot[job-queue]' for background scheduler."
        )

    return application


def main() -> None:
    """Start the bot."""
    print("=" * 60)
    print("  Amazon & Flipkart Price Tracker Bot (Raspberry Pi Edition)")
    print("=" * 60)
    app = build_application()
    app.run_polling(
        drop_pending_updates=True,
        bootstrap_retries=TELEGRAM_BOOTSTRAP_RETRIES,
        timeout=30,
    )


if __name__ == "__main__":
    main()
