"""Background price checking and alert scheduler."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError

from config import ALERT_ON_ANY_PRICE_CHANGE, MAX_CONCURRENT_REQUESTS
import providers
from storage import StorageManager
from utils.formatter import format_currency

logger = logging.getLogger(__name__)


class PriceCheckerScheduler:
    """Manages scheduled deduplicated price checking and Telegram alerting."""

    def __init__(self, bot=None, storage: Optional[StorageManager] = None):
        self.bot = bot
        self.storage = storage or StorageManager.get_instance()
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        self._is_checking: bool = False

    def set_bot(self, bot) -> None:
        """Attach Telegram bot instance for sending alerts."""
        self.bot = bot

    async def check_all_prices(self) -> Dict[str, Any]:
        """Perform a deduplicated price check across all actively tracked products.

        N users tracking 1 product = 1 HTTP request.
        """
        if self._is_checking:
            logger.info("Price check already in progress. Skipping duplicate run.")
            return {"status": "skipped", "reason": "in_progress"}

        self._is_checking = True
        logger.info("Starting scheduled price check...")

        stats = {
            "products_checked": 0,
            "alerts_sent": 0,
            "errors": 0,
        }

        try:
            unique_products = await self.storage.get_unique_tracked_products()
            if not unique_products:
                logger.info("No active tracked products found.")
                return stats

            # Check products with concurrency limiter
            tasks = [
                self._check_single_product(product_key, prod_data, stats)
                for product_key, prod_data in unique_products.items()
            ]
            await asyncio.gather(*tasks, return_exceptions=True)

            # Persist dirty state to disk
            await self.storage.save_if_dirty()
            logger.info("Finished scheduled check: %s", stats)

        except Exception as e:
            logger.error("Unexpected error in price checking job: %s", e)
        finally:
            self._is_checking = False

        return stats

    async def _check_single_product(
        self,
        product_key: str,
        prod_data: Dict[str, Any],
        stats: Dict[str, Any],
    ) -> None:
        """Fetch fresh price for one unique product and evaluate user alerts."""
        async with self._semaphore:
            # Gentle pacing to avoid anti-bot triggers on low-resource hardware
            await asyncio.sleep(0.5)

            url = prod_data.get("url")
            if not url:
                return

            provider = providers.get_provider(url)
            if not provider:
                return

            try:
                fresh_info = await provider.get_product(url)
                if not fresh_info:
                    stats["errors"] += 1
                    return

                stats["products_checked"] += 1
                new_price = fresh_info.price
                old_price = prod_data.get("price")

                # Update product in storage (also updates price history if changed)
                await self.storage.upsert_product(product_key, fresh_info.to_dict())

                price_changed = (old_price is not None and new_price != old_price)

                # Check all users tracking this product
                users_tracking = await self.storage.get_users_tracking_product(product_key)
                for user_id, track_info in users_tracking:
                    target_price = track_info.get("target_price")
                    last_alert = track_info.get("last_alert_price")

                    target_reached = (
                        target_price is not None
                        and new_price <= target_price
                    )

                    # Alert condition:
                    # 1. Price changed (up or down) from previous check
                    # 2. Or target reached for the first time at this price level
                    should_alert = False
                    if ALERT_ON_ANY_PRICE_CHANGE and price_changed:
                        should_alert = True
                    elif target_reached and new_price != last_alert:
                        should_alert = True

                    if should_alert:
                        sent = await self._send_alert(
                            user_id=user_id,
                            product_key=product_key,
                            product_info=fresh_info,
                            old_price=old_price,
                            target_price=target_price,
                        )
                        if sent:
                            stats["alerts_sent"] += 1
                            await self.storage.set_last_alert_price(user_id, product_key, new_price)

            except Exception as e:
                logger.error("Error checking %s: %s", product_key, e)
                stats["errors"] += 1

    async def _send_alert(
        self,
        user_id: str,
        product_key: str,
        product_info: Any,
        old_price: Optional[float],
        target_price: Optional[float],
    ) -> bool:
        """Send formatted alert message with Telegram inline keyboard."""
        if not self.bot:
            logger.warning("Bot instance not set; cannot send alert to %s", user_id)
            return False

        cur_str = format_currency(product_info.price, product_info.currency)
        platform_name = product_info.platform.capitalize()
        target_str = format_currency(target_price, product_info.currency) if target_price else "Not set"
        target_reached = (target_price is not None and product_info.price <= target_price)

        if old_price is not None and product_info.price != old_price:
            diff = product_info.price - old_price
            diff_abs_str = format_currency(abs(diff), product_info.currency)
            old_str = format_currency(old_price, product_info.currency)

            if diff < 0:
                # Price dropped
                if target_reached:
                    header = "🎯 <b>TARGET REACHED & PRICE DROP!</b>"
                    footer = f"🎉 <i>Your target price ({target_str}) has been reached!</i>"
                else:
                    header = "📉 <b>PRICE DROP ALERT!</b>"
                    footer = f"🎯 <b>Target:</b> {target_str}"

                message = (
                    f"{header}\n\n"
                    f"📦 <b>{product_info.title}</b>\n\n"
                    f"💰 <b>Current Price:</b> {cur_str} (⬇ -{diff_abs_str})\n"
                    f"📊 <b>Previous Price:</b> {old_str}\n"
                    f"🏪 <b>Platform:</b> {platform_name}\n\n"
                    f"{footer}"
                )
            else:
                # Price increased
                message = (
                    f"📈 <b>PRICE INCREASE NOTICE</b>\n\n"
                    f"📦 <b>{product_info.title}</b>\n\n"
                    f"💰 <b>Current Price:</b> {cur_str} (⬆ +{diff_abs_str})\n"
                    f"📊 <b>Previous Price:</b> {old_str}\n"
                    f"🎯 <b>Target:</b> {target_str}\n"
                    f"🏪 <b>Platform:</b> {platform_name}"
                )
        else:
            message = (
                f"🎯 <b>PRICE TARGET REACHED!</b>\n\n"
                f"📦 <b>{product_info.title}</b>\n\n"
                f"💰 <b>Current Price:</b> {cur_str}\n"
                f"🎯 <b>Target Price:</b> {target_str}\n"
                f"🏪 <b>Platform:</b> {platform_name}\n\n"
                f"📉 <i>Your target price has been reached!</i>"
            )

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛒 Open Product", url=product_info.url)]
        ])

        target_chat_id = int(user_id) if str(user_id).lstrip("-").isdigit() else user_id

        try:
            await self.bot.send_message(
                chat_id=target_chat_id,
                text=message,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            logger.info("Sent price alert to user %s for %s", user_id, product_key)
            return True
        except Exception as e:
            logger.error("Failed to send alert to user %s: %s", user_id, e)
            return False

    async def check_user_items_now(self, user_id: int | str) -> List[Dict[str, Any]]:
        """Manually trigger immediate price check for a specific user (for /check)."""
        uid = str(user_id)
        user_items = await self.storage.get_user_tracking(uid)
        results = []

        for item in user_items:
            product_key = item["product_key"]
            url = item.get("product", {}).get("url")
            if not url:
                continue

            provider = providers.get_provider(url)
            if not provider:
                continue

            try:
                fresh = await provider.get_product(url)
                if fresh:
                    await self.storage.upsert_product(product_key, fresh.to_dict())
                    results.append({
                        "product_key": product_key,
                        "title": fresh.title,
                        "url": fresh.url,
                        "old_price": item.get("product", {}).get("price"),
                        "new_price": fresh.price,
                        "target_price": item.get("target_price"),
                    })
            except Exception as e:
                logger.error("Error during manual check for %s: %s", product_key, e)

        await self.storage.save_if_dirty()
        return results
