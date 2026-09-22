"""Tracker service coordinating user actions and storage."""

import logging
from typing import Any, Dict, List, Optional, Tuple

import providers
from providers.base import ProductInfo
from storage import StorageManager
from utils.formatter import format_currency

logger = logging.getLogger(__name__)


class TrackerService:
    """Business logic for managing tracking, listing, removing, and history."""

    def __init__(self, storage: Optional[StorageManager] = None):
        self.storage = storage or StorageManager.get_instance()

    async def add_product_tracking(
        self,
        user_id: int | str,
        url: str,
        target_price: float,
        prefetched_product: Optional[ProductInfo] = None,
    ) -> Tuple[bool, str, Optional[ProductInfo]]:
        """Add or update tracking for a user.

        Returns (success, message, ProductInfo).
        """
        provider = providers.get_provider(url)
        if not provider:
            return False, "❌ Unsupported URL. Only Amazon India and Flipkart links are supported.", None

        product_info = prefetched_product
        if not product_info:
            try:
                product_info = await provider.get_product(url)
            except Exception as e:
                logger.error("Failed to fetch product for %s: %s", url, e)
                return False, f"⚠️ Failed to fetch product details: {e}", None

        if not product_info:
            return False, "⚠️ Could not retrieve product details or current price from this link.", None

        product_key = product_info.product_key

        # Save to storage
        await self.storage.register_user(user_id)
        await self.storage.upsert_product(product_key, product_info.to_dict())
        await self.storage.add_tracking(user_id, product_key, target_price)

        return True, "✅ Tracking saved successfully.", product_info

    async def get_user_items(self, user_id: int | str) -> List[Dict[str, Any]]:
        """Retrieve user's tracked products."""
        return await self.storage.get_user_tracking(user_id)

    async def remove_user_item(
        self,
        user_id: int | str,
        identifier: str,
    ) -> Tuple[bool, str]:
        """Remove an item by index number (1-based from /list) or product_key."""
        items = await self.storage.get_user_tracking(user_id)
        if not items:
            return False, "You are not tracking any products."

        target_key: Optional[str] = None

        # Check if identifier is an index number (e.g. '1', '2')
        if identifier.isdigit():
            idx = int(identifier) - 1
            if 0 <= idx < len(items):
                target_key = items[idx]["product_key"]
            else:
                return False, f"Invalid number. Choose between 1 and {len(items)}."
        else:
            # Check if identifier matches product_key
            for item in items:
                if item["product_key"] == identifier:
                    target_key = identifier
                    break

        if not target_key:
            return False, "Item not found in your tracking list."

        removed = await self.storage.remove_tracking(user_id, target_key)
        if removed:
            return True, "Product removed from tracking."
        return False, "Failed to remove product."

    async def get_item_history(
        self,
        user_id: int | str,
        identifier: str,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """Get history points and statistics for a tracked product."""
        items = await self.storage.get_user_tracking(user_id)
        if not items:
            return False, "You are not tracking any products.", None

        target_item: Optional[Dict[str, Any]] = None
        if identifier.isdigit():
            idx = int(identifier) - 1
            if 0 <= idx < len(items):
                target_item = items[idx]
            else:
                return False, f"Invalid number. Choose between 1 and {len(items)}.", None
        else:
            for item in items:
                if item["product_key"] == identifier:
                    target_item = item
                    break

        if not target_item:
            return False, "Item not found in your tracking list.", None

        product_key = target_item["product_key"]
        history_points = await self.storage.get_price_history(product_key)

        return True, "OK", {
            "product": target_item.get("product", {}),
            "target_price": target_item.get("target_price"),
            "history": history_points,
        }
