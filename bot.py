"""Main Telegram Bot application for Amazon & Flipkart price tracking.

Optimized for Raspberry Pi Zero 2 W (single async process, low RAM footprint).
Features rich Obsidian Telemetry UX with interactive inline keyboards,
one-tap target presets, price trails, and rapid manual polling.
"""

import asyncio
import logging
import time
from typing import Any, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from config import (
    CHECK_INTERVAL_HOURS,
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
from utils.formatter import (
    calculate_discount_percent,
    clean_title,
    format_currency,
    format_history_report,
    parse_price_text,
)
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
    """Handle /start command with interactive quick launch buttons."""
    if not update.effective_user:
        return ConversationHandler.END

    await storage.register_user(update.effective_user.id)
    welcome_text = (
        "👋 <b>Welcome to PiPriceTracker!</b>\n"
        "<i>Built lean for Raspberry Pi Zero 2 W • Zero-DB Architecture</i>\n\n"
        "I track prices on <b>Amazon India</b> & <b>Flipkart</b> with non-blocking "
        "POSIX persistence and ping you immediately upon price drops.\n\n"
        "<b>⚡ Quick Start:</b>\n"
        "1. Paste any Amazon or Flipkart product link.\n"
        "2. Choose a quick threshold preset (-5%, -10%) or enter a custom target.\n"
        "3. Rest easy! Background cron alerts you when the price drops.\n\n"
        "<b>🛠️ Commands:</b>\n"
        "• <code>/list</code> — View tracked items & targets\n"
        "• <code>/check</code> — Trigger instant price refresh\n"
        "• <code>/history &lt;n&gt;</code> — View price fluctuation trail\n"
        "• <code>/remove &lt;n&gt;</code> — Disarm tracking for an item\n"
        "• <code>/help</code> — Full manual guide"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📋 My Watchlist", callback_data="nav_list"),
            InlineKeyboardButton("⚡ Check Prices", callback_data="nav_check"),
        ],
        [
            InlineKeyboardButton("📖 Help & Guide", callback_data="nav_help")
        ]
    ])

    if update.message:
        await update.message.reply_text(welcome_text, parse_mode="HTML", reply_markup=keyboard)
    elif update.callback_query and update.callback_query.message:
        await update.callback_query.message.edit_text(welcome_text, parse_mode="HTML", reply_markup=keyboard)

    return ConversationHandler.END


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /help command with interactive options."""
    help_text = (
        "📖 <b>PiPriceTracker Bot Guide</b>\n\n"
        "• <b>Send a Link:</b> Paste any Amazon.in or Flipkart product URL.\n"
        "• <b>/list:</b> View all products currently monitored in your watchlist.\n"
        "• <b>/check:</b> Trigger an immediate sequential price poll.\n"
        "• <b>/history &lt;n&gt;:</b> Inspect recent price points & net change.\n"
        "• <b>/remove &lt;n&gt;:</b> Untrack an item by index number (e.g. <code>/remove 1</code>).\n"
        "• <b>/cancel:</b> Cancel an in-flight product addition.\n\n"
        "💡 <i>Tip: Checks run automatically thrice a day (every "
        f"{CHECK_INTERVAL_HOURS} hours), alerting you on every price drop.</i>"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📋 View Watchlist", callback_data="nav_list"),
            InlineKeyboardButton("⚡ Run Price Check", callback_data="nav_check"),
        ]
    ])

    if update.message:
        await update.message.reply_text(help_text, parse_mode="HTML", reply_markup=keyboard)
    elif update.callback_query and update.callback_query.message:
        await update.callback_query.message.edit_text(help_text, parse_mode="HTML", reply_markup=keyboard)

    return ConversationHandler.END


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /list command to display tracked products with action buttons."""
    user_id = update.effective_user.id if update.effective_user else None
    if not user_id:
        return ConversationHandler.END

    items = await tracker.get_user_items(user_id)
    if not items:
        empty_text = (
            "📦 <b>You are not tracking any products yet.</b>\n\n"
            "Send me an Amazon or Flipkart link to start tracking!"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📖 Help Guide", callback_data="nav_help")]
        ])
        if update.message:
            await update.message.reply_text(empty_text, parse_mode="HTML", reply_markup=keyboard)
        elif update.callback_query and update.callback_query.message:
            await update.callback_query.message.edit_text(empty_text, parse_mode="HTML", reply_markup=keyboard)
        return ConversationHandler.END

    lines = [f"📋 <b>Your Tracked Products ({len(items)}):</b>\n"]
    buttons = []

    for idx, item in enumerate(items, 1):
        prod = item.get("product", {})
        title = prod.get("title", "Product")
        platform = prod.get("platform", "Unknown").capitalize()
        curr_price = format_currency(prod.get("price"))
        target_price = format_currency(item.get("target_price"))
        url = prod.get("url", "")
        p_val = prod.get("price")
        t_val = item.get("target_price")

        status_tag = "🎯 Target Met!" if (p_val and t_val and p_val <= t_val) else "⏳ Active"

        lines.append(
            f"<b>{idx}. {title}</b>\n"
            f"   🏪 <code>{platform} Verified</code> • <b>{status_tag}</b>\n"
            f"   💰 Current: <b>{curr_price}</b>  |  🎯 Target: <b>{target_price}</b>\n"
        )

        row = []
        if url:
            row.append(InlineKeyboardButton(f"🛒 #{idx}", url=url))
        row.append(InlineKeyboardButton(f"📊 Hist #{idx}", callback_data=f"hist_{idx}"))
        row.append(InlineKeyboardButton(f"❌ Del #{idx}", callback_data=f"rem_{idx}"))
        buttons.append(row)

    buttons.append([
        InlineKeyboardButton("🔄 Check Prices Now", callback_data="nav_check")
    ])

    lines.append("Use <code>/remove &lt;number&gt;</code> to stop tracking.")
    lines.append("Use <code>/history &lt;number&gt;</code> to view price history.")

    text_body = "\n".join(lines)
    keyboard = InlineKeyboardMarkup(buttons)

    if update.message:
        await update.message.reply_text(text_body, parse_mode="HTML", reply_markup=keyboard, disable_web_page_preview=True)
    elif update.callback_query and update.callback_query.message:
        await update.callback_query.message.edit_text(text_body, parse_mode="HTML", reply_markup=keyboard, disable_web_page_preview=True)

    return ConversationHandler.END


async def remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /remove <number> command."""
    user_id = update.effective_user.id if update.effective_user else None
    if not user_id:
        return ConversationHandler.END

    items = await tracker.get_user_items(user_id)
    if not items:
        msg = "📦 You are not tracking any products."
        if update.message:
            await update.message.reply_text(msg)
        elif update.callback_query and update.callback_query.message:
            await update.callback_query.message.edit_text(msg)
        return ConversationHandler.END

    if not context.args:
        # Show interactive selection buttons
        buttons = []
        for idx, item in enumerate(items, 1):
            prod_title = clean_title(item.get("product", {}).get("title", f"Product #{idx}"), max_length=24)
            buttons.append([InlineKeyboardButton(f"🗑️ #{idx}. {prod_title}", callback_data=f"rem_{idx}")])
        buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="nav_list")])

        prompt = (
            "Please specify which item to remove from your list.\n"
            "Example: <code>/remove 1</code>\n\n"
            "Use <code>/list</code> to see item numbers."
        )
        keyboard = InlineKeyboardMarkup(buttons)
        if update.message:
            await update.message.reply_text(prompt, parse_mode="HTML", reply_markup=keyboard)
        elif update.callback_query and update.callback_query.message:
            await update.callback_query.message.edit_text(prompt, parse_mode="HTML", reply_markup=keyboard)
        return ConversationHandler.END

    identifier = context.args[0].strip()
    return await execute_removal(update, user_id, identifier)


async def execute_removal(update: Update, user_id: int | str, identifier: str) -> int:
    """Execute product untracking and display confirmation."""
    success, msg = await tracker.remove_user_item(user_id, identifier)
    reply_text = f"{'✅' if success else '❌'} {msg}"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 View Updated List", callback_data="nav_list")]
    ])

    if update.message:
        await update.message.reply_text(reply_text, reply_markup=keyboard)
    elif update.callback_query and update.callback_query.message:
        await update.callback_query.message.edit_text(reply_text, reply_markup=keyboard)

    return ConversationHandler.END


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /history <number> command."""
    user_id = update.effective_user.id if update.effective_user else None
    if not user_id:
        return ConversationHandler.END

    items = await tracker.get_user_items(user_id)
    if not items:
        msg = "📦 You are not tracking any products yet."
        if update.message:
            await update.message.reply_text(msg)
        elif update.callback_query and update.callback_query.message:
            await update.callback_query.message.edit_text(msg)
        return ConversationHandler.END

    if not context.args:
        buttons = []
        for idx, item in enumerate(items, 1):
            prod_title = clean_title(item.get("product", {}).get("title", f"Product #{idx}"), max_length=24)
            buttons.append([InlineKeyboardButton(f"📊 {idx}. {prod_title}", callback_data=f"hist_{idx}")])
        buttons.append([InlineKeyboardButton("⬅️ Back to List", callback_data="nav_list")])

        prompt = (
            "Please specify which item you want price history for.\n"
            "Example: <code>/history 1</code>\n\n"
            "Use <code>/list</code> to see item numbers."
        )
        keyboard = InlineKeyboardMarkup(buttons)
        if update.message:
            await update.message.reply_text(prompt, parse_mode="HTML", reply_markup=keyboard)
        elif update.callback_query and update.callback_query.message:
            await update.callback_query.message.edit_text(prompt, parse_mode="HTML", reply_markup=keyboard)
        return ConversationHandler.END

    identifier = context.args[0].strip()
    return await render_history_view(update, user_id, identifier)


async def render_history_view(update: Update, user_id: int | str, identifier: str) -> int:
    """Render detailed price history report for an item."""
    success, msg, data = await tracker.get_item_history(user_id, identifier)
    if not success or not data:
        err_msg = f"❌ {msg}"
        if update.message:
            await update.message.reply_text(err_msg)
        elif update.callback_query and update.callback_query.message:
            await update.callback_query.message.edit_text(err_msg)
        return ConversationHandler.END

    prod = data.get("product", {})
    title = prod.get("title", "Product")
    platform = prod.get("platform", "Unknown").capitalize()
    curr_price = format_currency(prod.get("price"))
    target_price = format_currency(data.get("target_price"))
    url = prod.get("url", "")
    history_points = data.get("history", [])

    report = format_history_report(history_points)
    lines = [
        f"📊 <b>{title}</b>\n\n",
        f"🏪 <code>{platform} Verified</code>\n",
        f"💰 Current: <b>{curr_price}</b>  |  🎯 Target: <b>{target_price}</b>\n\n",
        f"<b>Recent price checks ({len(history_points)}):</b>\n",
        report,
    ]

    buttons = []
    btn_row = []
    if url:
        btn_row.append(InlineKeyboardButton(f"🛒 Open on {platform}", url=url))
    btn_row.append(InlineKeyboardButton("⬅️ Back to List", callback_data="nav_list"))
    buttons.append(btn_row)

    keyboard = InlineKeyboardMarkup(buttons)
    text_content = "".join(lines)

    if update.message:
        await update.message.reply_text(text_content, parse_mode="HTML", reply_markup=keyboard, disable_web_page_preview=True)
    elif update.callback_query and update.callback_query.message:
        await update.callback_query.message.edit_text(text_content, parse_mode="HTML", reply_markup=keyboard, disable_web_page_preview=True)

    return ConversationHandler.END


async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /check command to immediately refresh prices for this user."""
    user_id = update.effective_user.id if update.effective_user else None
    if not user_id:
        return ConversationHandler.END

    loading_text = "🔄 Checking current prices for your tracked items..."
    if update.message:
        status_msg = await update.message.reply_text(loading_text)
    elif update.callback_query and update.callback_query.message:
        status_msg = update.callback_query.message
        await status_msg.edit_text(loading_text)
    else:
        return ConversationHandler.END

    start_time = time.monotonic()
    results = await scheduler_service.check_user_items_now(user_id)
    duration = time.monotonic() - start_time

    if not results:
        empty_text = (
            "📦 You have no tracked items to check.\n"
            "Send an Amazon or Flipkart link to start tracking!"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📖 Help Guide", callback_data="nav_help")]
        ])
        await status_msg.edit_text(empty_text, reply_markup=keyboard)
        return ConversationHandler.END

    lines = ["🔍 <b>Price check results:</b>\n"]
    for idx, res in enumerate(results, 1):
        title = res.get("title", "Product")
        old_p = res.get("old_price")
        new_p = res.get("new_price")
        target_p = res.get("target_price")

        target_reached = (target_p is not None and new_p <= target_p)
        tag = " 🎯 [Target Met!]" if target_reached else ""

        if old_p and old_p != new_p:
            diff = new_p - old_p
            diff_str = f" ({'+' if diff > 0 else ''}{format_currency(diff)})"
            price_text = f"{format_currency(old_p)} → {format_currency(new_p)}{diff_str}"
        else:
            price_text = f"{format_currency(new_p)}"

        lines.append(
            f"<b>{idx}. {title}</b>\n"
            f"   💰 Current: {price_text}{tag} | 🎯 Target: {format_currency(target_p)}\n"
        )

    lines.append(f"\n⏱️ <i>Checked in {duration:.2f}s • Semaphore(2) Throttle • Memory Safe</i>")

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 Refresh Again", callback_data="nav_check"),
            InlineKeyboardButton("📋 View Watchlist", callback_data="nav_list"),
        ]
    ])

    await status_msg.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=keyboard, disable_web_page_preview=True)
    return ConversationHandler.END


# --- URL & Target Price Conversation Flow ---

async def handle_url_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Extract URL, fetch product details, and prompt for target price with presets."""
    if not update.message or not update.message.text:
        return ConversationHandler.END

    text = update.message.text.strip()
    urls = extract_urls(text)
    if not urls:
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

    start_time = time.monotonic()
    try:
        product_info = await provider.get_product(target_url)
    except Exception as e:
        logger.error("Error fetching product: %s", e)
        product_info = None
    fetch_latency = time.monotonic() - start_time

    if not product_info:
        await fetch_msg.edit_text(
            "⚠️ <b>Could not retrieve product details.</b>\n"
            "Please check that the link is an active product page and try again.",
            parse_mode="HTML",
        )
        return ConversationHandler.END

    # Save pending product in context for this user
    context.user_data["pending_product"] = product_info

    cur_str = format_currency(product_info.price, product_info.currency)
    platform_name = product_info.platform.capitalize()

    disc_str = ""
    if product_info.original_price and product_info.original_price > product_info.price:
        orig_str = format_currency(product_info.original_price, product_info.currency)
        pct = calculate_discount_percent(product_info.original_price, product_info.price)
        disc_str = f"  <s>{orig_str}</s>  <code>-{pct}%</code>"

    prompt_text = (
        f"🎧 <b>{product_info.title}</b>\n\n"
        f"🏪 <b>Platform:</b> <code>{platform_name} Verified</code>\n"
        f"💰 <b>Current price:</b> {cur_str}{disc_str}\n\n"
        f"What price should I alert you at?\n\n"
        f"<i>Tap a quick preset below or type a custom number:</i>\n\n"
        f"<i>Example:</i>\n"
        f"<code>{int(product_info.price * 0.9)}</code>\n\n"
        f"<i>⏱️ <code>{fetch_latency:.2f}s fetch</code> • (Send /cancel to abort)</i>"
    )

    # Preset drop targets: -5%, -10%, -15%
    p = product_info.price
    t5 = int(round(p * 0.95))
    t10 = int(round(p * 0.90))
    t15 = int(round(p * 0.85))

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"-5% ({format_currency(t5)})", callback_data=f"preset_{t5}"),
            InlineKeyboardButton(f"-10% ({format_currency(t10)})", callback_data=f"preset_{t10}"),
            InlineKeyboardButton(f"-15% ({format_currency(t15)})", callback_data=f"preset_{t15}"),
        ],
        [
            InlineKeyboardButton("❌ Cancel", callback_data="cancel_action")
        ]
    ])

    await fetch_msg.edit_text(prompt_text, parse_mode="HTML", reply_markup=keyboard)
    return WAITING_FOR_TARGET_PRICE


async def handle_preset_target(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle one-tap preset button clicks during target price selection."""
    query = update.callback_query
    if not query:
        return ConversationHandler.END

    await query.answer()

    if query.data == "cancel_action":
        context.user_data.pop("pending_product", None)
        await query.message.edit_text("Action cancelled.")
        return ConversationHandler.END

    if query.data and query.data.startswith("preset_"):
        target_val = float(query.data.split("_")[1])
        return await register_tracking_and_confirm(
            message_or_query=query,
            user_id=update.effective_user.id,
            target_price=target_val,
            context=context,
        )

    return WAITING_FOR_TARGET_PRICE


async def handle_target_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive target price from user text message, save tracking, and confirm."""
    if not update.effective_user or not update.message or not update.message.text:
        return ConversationHandler.END

    text = update.message.text.strip()
    target_price = parse_price_text(text)

    product_info: Optional[ProductInfo] = context.user_data.get("pending_product")
    if not product_info:
        await update.message.reply_text("⚠️ Session expired. Please send the product link again.")
        return ConversationHandler.END

    if target_price is None or target_price <= 0:
        await update.message.reply_text(
            "❌ Please enter a valid positive number for your target price.\n"
            "Example: <code>25000</code>\n"
            "Or send /cancel to abort.",
            parse_mode="HTML",
        )
        return WAITING_FOR_TARGET_PRICE

    return await register_tracking_and_confirm(
        message_or_query=update.message,
        user_id=update.effective_user.id,
        target_price=target_price,
        context=context,
    )


async def register_tracking_and_confirm(
    message_or_query: Any,
    user_id: int | str,
    target_price: float,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """Save tracking to storage and send confirmation with action button."""
    product_info: Optional[ProductInfo] = context.user_data.get("pending_product")
    if not product_info:
        msg = "⚠️ Session expired. Please send the product link again."
        if hasattr(message_or_query, "reply_text"):
            await message_or_query.reply_text(msg)
        elif hasattr(message_or_query, "message"):
            await message_or_query.message.edit_text(msg)
        return ConversationHandler.END

    # Save tracking
    success, msg, _ = await tracker.add_product_tracking(
        user_id=user_id,
        url=product_info.url,
        target_price=target_price,
        prefetched_product=product_info,
    )

    # Clear pending data
    context.user_data.pop("pending_product", None)

    if success:
        platform_name = product_info.platform.capitalize()
        cur_str = format_currency(product_info.price, product_info.currency)
        target_str = format_currency(target_price, product_info.currency)

        confirm_text = (
            "✅ <b>Tracking started!</b>\n\n"
            f"🎧 <b>{product_info.title}</b>\n\n"
            f"Current: {cur_str}\n"
            f"Target: {target_str}\n\n"
            f"<i>I'll notify you when the price reaches your target.</i>\n\n"
            f"⏱️ <b>NEXT POLL:</b> <code>Every {CHECK_INTERVAL_HOURS}h</code> | <code>CRON: ACTIVE</code>"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"🛒 Open Product on {platform_name}", url=product_info.url)],
            [InlineKeyboardButton("📋 View Watchlist", callback_data="nav_list")]
        ])

        if hasattr(message_or_query, "reply_text"):
            await message_or_query.reply_text(confirm_text, parse_mode="HTML", reply_markup=keyboard)
        elif hasattr(message_or_query, "message"):
            await message_or_query.message.edit_text(confirm_text, parse_mode="HTML", reply_markup=keyboard)
    else:
        err = f"❌ {msg}"
        if hasattr(message_or_query, "reply_text"):
            await message_or_query.reply_text(err)
        elif hasattr(message_or_query, "message"):
            await message_or_query.message.edit_text(err)

    return ConversationHandler.END


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel active conversation."""
    context.user_data.pop("pending_product", None)
    if update.message:
        await update.message.reply_text("Action cancelled.")
    elif update.callback_query and update.callback_query.message:
        await update.callback_query.message.edit_text("Action cancelled.")
    return ConversationHandler.END


# --- General Callback Handler (Buttons outside conversations) ---

async def general_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle all button clicks outside active conversations."""
    query = update.callback_query
    if not query:
        return

    await query.answer()
    data = query.data or ""
    user_id = update.effective_user.id if update.effective_user else None
    if not user_id:
        return

    if data == "nav_list":
        await list_command(update, context)
    elif data == "nav_check":
        await check_command(update, context)
    elif data == "nav_help":
        await help_command(update, context)
    elif data.startswith("hist_"):
        identifier = data.split("_", 1)[1]
        await render_history_view(update, user_id, identifier)
    elif data.startswith("rem_"):
        identifier = data.split("_", 1)[1]
        await execute_removal(update, user_id, identifier)
    elif data.startswith("rem_key:"):
        key = data.split(":", 1)[1]
        await execute_removal(update, user_id, key)
    elif data.startswith("hist_key:"):
        key = data.split(":", 1)[1]
        await render_history_view(update, user_id, key)


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
                CallbackQueryHandler(handle_preset_target, pattern=r"^(preset_\d+|cancel_action)$"),
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
    application.add_handler(CallbackQueryHandler(general_callback_handler))

    # Setup Scheduler (run every CHECK_INTERVAL_MINUTES)
    if application.job_queue:
        interval_seconds = CHECK_INTERVAL_MINUTES * 60
        application.job_queue.run_repeating(
            scheduled_check_job,
            interval=interval_seconds,
            first=30,  # First check 30 seconds after bot boot
        )
        logger.info(
            "Scheduled repeating price check job every %d hours (%d minutes) - thrice a day",
            CHECK_INTERVAL_HOURS,
            CHECK_INTERVAL_MINUTES,
        )
    else:
        logger.warning(
            "JobQueue is not enabled in python-telegram-bot! "
            "Install 'python-telegram-bot[job-queue]' for background scheduler."
        )

    return application


def main() -> None:
    """Start the bot."""
    print("=" * 60)
    print("  PiPriceTracker • Lightweight Telegram Bot (Raspberry Pi Edition)")
    print("=" * 60)
    app = build_application()
    app.run_polling(
        drop_pending_updates=True,
        bootstrap_retries=TELEGRAM_BOOTSTRAP_RETRIES,
        timeout=30,
    )


if __name__ == "__main__":
    main()
