"""Atomic, thread-safe JSON storage engine.

Optimized for Raspberry Pi Zero 2 W:
- In-memory cache to eliminate repetitive SD card reads.
- Atomic file write: write temp -> flush -> fsync -> atomic rename.
- Write-only-on-dirty to reduce SD card wear.
- Automatic daily backups with retention pruning.
- Automatic orphaned product cleanup to keep memory and disk footprint tiny.
"""

import asyncio
import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import config

logger = logging.getLogger(__name__)


class StorageManager:
    """Thread-safe and async-safe JSON storage manager with atomic writes."""

    _instance: Optional["StorageManager"] = None

    def __init__(self, file_path: Optional[Path] = None):
        self.file_path: Path = file_path or config.STORAGE_FILE
        self.backup_dir: Path = self.file_path.parent / "backup"
        self._lock = asyncio.Lock()
        self._data: Optional[Dict[str, Any]] = None
        self._dirty: bool = False
        self._last_backup_date: Optional[str] = None

    @classmethod
    def get_instance(cls, file_path: Optional[Path] = None) -> "StorageManager":
        """Singleton accessor."""
        if cls._instance is None:
            cls._instance = cls(file_path=file_path)
        return cls._instance

    def _default_schema(self) -> Dict[str, Any]:
        return {
            "users": {},
            "products": {},
            "tracking": {},
            "history": {},
        }

    async def initialize(self) -> None:
        """Load data from disk or initialize empty schema."""
        async with self._lock:
            await self._load_data()

    async def _load_data(self) -> None:
        if self._data is not None:
            return

        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        if self.file_path.exists():
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    # Validate top-level keys
                    schema = self._default_schema()
                    for key in schema:
                        if key not in data or not isinstance(data[key], dict):
                            data[key] = schema[key]
                    self._data = data
                    logger.info("Storage loaded successfully from %s", self.file_path)
                    return
            except Exception as e:
                logger.error("Failed to load %s: %s. Attempting backup restore...", self.file_path, e)
                restored = self._try_restore_backup()
                if restored:
                    self._data = restored
                    return
                logger.warning("No valid backup found. Initializing clean storage.")

        self._data = self._default_schema()
        self._dirty = True
        await self._save_to_disk()

    def _try_restore_backup(self) -> Optional[Dict[str, Any]]:
        """Attempt to restore the most recent backup."""
        if not self.backup_dir.exists():
            return None
        backups = sorted(self.backup_dir.glob("storage-*.json"), reverse=True)
        for backup_path in backups:
            try:
                with open(backup_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if "products" in data and "tracking" in data:
                        logger.info("Restored storage from backup: %s", backup_path)
                        return data
            except Exception:
                continue
        return None

    async def save_if_dirty(self) -> None:
        """Persist data if changes occurred."""
        async with self._lock:
            if self._dirty:
                await self._save_to_disk()

    async def _save_to_disk(self) -> None:
        """Perform atomic write and optional daily backup."""
        if self._data is None:
            return

        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        temp_file = self.file_path.with_name(f"{self.file_path.name}.tmp")

        try:
            # Atomic write: write to temp -> flush -> fsync -> rename
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())

            # Atomic replace (POSIX guarantees atomicity)
            os.replace(temp_file, self.file_path)
            self._dirty = False

            # Daily backup rotation
            self._maybe_create_backup()

        except Exception as e:
            logger.error("Failed atomic write to %s: %s", self.file_path, e)
            if temp_file.exists():
                try:
                    temp_file.unlink()
                except OSError:
                    pass
            raise

    def _maybe_create_backup(self) -> None:
        """Create a daily backup file if not already done today."""
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        if self._last_backup_date == today:
            return

        try:
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            backup_path = self.backup_dir / f"storage-{today}.json"
            shutil.copy2(self.file_path, backup_path)
            self._last_backup_date = today

            # Retention: keep only latest MAX_BACKUPS files
            all_backups = sorted(self.backup_dir.glob("storage-*.json"))
            if len(all_backups) > config.MAX_BACKUPS:
                for old in all_backups[:-config.MAX_BACKUPS]:
                    try:
                        old.unlink()
                    except OSError:
                        pass
        except Exception as e:
            logger.warning("Failed creating daily backup: %s", e)

    # --- Data Operations (Protected by _lock) ---

    async def register_user(self, user_id: int | str) -> None:
        """Register a user if not already recorded."""
        uid = str(user_id)
        async with self._lock:
            await self._load_data()
            assert self._data is not None
            if uid not in self._data["users"]:
                self._data["users"][uid] = {
                    "created_at": datetime.now(timezone.utc).isoformat()
                }
                self._dirty = True
                await self._save_to_disk()

    async def upsert_product(self, product_key: str, product_data: Dict[str, Any]) -> None:
        """Update or insert a product's details and record price in history."""
        async with self._lock:
            await self._load_data()
            assert self._data is not None

            old_product = self._data["products"].get(product_key)
            new_price = product_data.get("price")

            # Check if actual change occurred
            price_changed = old_product is None or old_product.get("price") != new_price
            self._data["products"][product_key] = product_data

            if price_changed and new_price is not None:
                # Update history
                if product_key not in self._data["history"]:
                    self._data["history"][product_key] = []
                history_list = self._data["history"][product_key]
                history_list.append({
                    "price": new_price,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                # Cap history to save RAM and SD card space
                if len(history_list) > config.MAX_HISTORY_ENTRIES:
                    self._data["history"][product_key] = history_list[-config.MAX_HISTORY_ENTRIES:]

            self._dirty = True
            await self._save_to_disk()

    async def add_tracking(
        self,
        user_id: int | str,
        product_key: str,
        target_price: float,
    ) -> None:
        """Add or update tracking for a specific user and product."""
        uid = str(user_id)
        async with self._lock:
            await self._load_data()
            assert self._data is not None

            if uid not in self._data["tracking"]:
                self._data["tracking"][uid] = {}

            self._data["tracking"][uid][product_key] = {
                "target_price": target_price,
                "alert_enabled": True,
                "last_alert_price": None,
                "added_at": datetime.now(timezone.utc).isoformat(),
            }
            self._dirty = True
            await self._save_to_disk()

    async def remove_tracking(self, user_id: int | str, product_key: str) -> bool:
        """Remove a product tracking record for a user. Cleans up orphaned products."""
        uid = str(user_id)
        async with self._lock:
            await self._load_data()
            assert self._data is not None

            user_tracking = self._data["tracking"].get(uid, {})
            if product_key in user_tracking:
                del user_tracking[product_key]
                self._dirty = True
                self._prune_orphans_locked()
                await self._save_to_disk()
                return True
            return False

    def _prune_orphans_locked(self) -> None:
        """Remove products and histories that are no longer tracked by any user."""
        if self._data is None:
            return
        active_keys: Set[str] = set()
        for u_track in self._data["tracking"].values():
            active_keys.update(u_track.keys())

        # Prune unreferenced products
        for prod_key in list(self._data["products"].keys()):
            if prod_key not in active_keys:
                del self._data["products"][prod_key]
                self._data["history"].pop(prod_key, None)
                self._dirty = True

    async def get_user_tracking(self, user_id: int | str) -> List[Dict[str, Any]]:
        """Get list of tracked products with their current product data."""
        uid = str(user_id)
        async with self._lock:
            await self._load_data()
            assert self._data is not None

            user_tracking = self._data["tracking"].get(uid, {})
            result = []
            for product_key, track_info in user_tracking.items():
                product_info = self._data["products"].get(product_key, {})
                result.append({
                    "product_key": product_key,
                    "target_price": track_info.get("target_price"),
                    "alert_enabled": track_info.get("alert_enabled", True),
                    "last_alert_price": track_info.get("last_alert_price"),
                    "product": product_info,
                })
            return result

    async def get_unique_tracked_products(self) -> Dict[str, Dict[str, Any]]:
        """Return dict of product_key -> product_data for all actively tracked products."""
        async with self._lock:
            await self._load_data()
            assert self._data is not None

            active_keys: Set[str] = set()
            for u_track in self._data["tracking"].values():
                for p_key, info in u_track.items():
                    if info.get("alert_enabled", True):
                        active_keys.add(p_key)

            return {
                key: self._data["products"][key]
                for key in active_keys
                if key in self._data["products"]
            }

    async def get_users_tracking_product(self, product_key: str) -> List[tuple[str, Dict[str, Any]]]:
        """Return list of (user_id, track_info) for users tracking this product."""
        async with self._lock:
            await self._load_data()
            assert self._data is not None

            users = []
            for uid, u_track in self._data["tracking"].items():
                if product_key in u_track and u_track[product_key].get("alert_enabled", True):
                    users.append((uid, u_track[product_key]))
            return users

    async def set_last_alert_price(self, user_id: int | str, product_key: str, price: float) -> None:
        """Update last_alert_price to prevent alert spam."""
        uid = str(user_id)
        async with self._lock:
            await self._load_data()
            assert self._data is not None

            if uid in self._data["tracking"] and product_key in self._data["tracking"][uid]:
                self._data["tracking"][uid][product_key]["last_alert_price"] = price
                self._dirty = True
                await self._save_to_disk()

    async def get_price_history(self, product_key: str) -> List[Dict[str, Any]]:
        """Return price history points for a product."""
        async with self._lock:
            await self._load_data()
            assert self._data is not None
            return list(self._data["history"].get(product_key, []))
