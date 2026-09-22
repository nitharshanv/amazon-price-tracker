"""Unit tests for the atomic StorageManager."""

import asyncio
import json
import pytest
from pathlib import Path
from storage import StorageManager


@pytest.fixture
def temp_storage_path(tmp_path: Path) -> Path:
    return tmp_path / "test_storage.json"


@pytest.mark.asyncio
async def test_storage_initialization(temp_storage_path: Path):
    storage = StorageManager(file_path=temp_storage_path)
    await storage.initialize()

    assert temp_storage_path.exists()
    with open(temp_storage_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        assert "users" in data
        assert "products" in data
        assert "tracking" in data
        assert "history" in data


@pytest.mark.asyncio
async def test_storage_add_and_remove_tracking(temp_storage_path: Path):
    storage = StorageManager(file_path=temp_storage_path)
    await storage.initialize()

    user_id = "12345"
    product_key = "amazon:B09XYZ1234"
    product_data = {
        "platform": "amazon",
        "product_id": "B09XYZ1234",
        "title": "Test Headphones",
        "url": "https://www.amazon.in/dp/B09XYZ1234",
        "price": 2999.0,
        "currency": "INR",
        "last_checked": "2026-09-22T00:00:00",
    }

    # Upsert product and add tracking
    await storage.register_user(user_id)
    await storage.upsert_product(product_key, product_data)
    await storage.add_tracking(user_id, product_key, target_price=2500.0)

    # Verify tracking retrieved
    items = await storage.get_user_tracking(user_id)
    assert len(items) == 1
    assert items[0]["product_key"] == product_key
    assert items[0]["target_price"] == 2500.0
    assert items[0]["product"]["title"] == "Test Headphones"

    # Verify unique tracked products
    unique = await storage.get_unique_tracked_products()
    assert product_key in unique

    # Remove tracking
    removed = await storage.remove_tracking(user_id, product_key)
    assert removed is True

    items_after = await storage.get_user_tracking(user_id)
    assert len(items_after) == 0

    # Verify orphan pruning cleaned up the product from storage
    unique_after = await storage.get_unique_tracked_products()
    assert product_key not in unique_after


@pytest.mark.asyncio
async def test_concurrent_writes(temp_storage_path: Path):
    """Verify asyncio.Lock prevents race conditions during rapid concurrent writes."""
    storage = StorageManager(file_path=temp_storage_path)
    await storage.initialize()

    async def add_dummy(i: int):
        await storage.register_user(f"user_{i}")
        await storage.upsert_product(
            f"amazon:B0000000{i:02d}",
            {"title": f"Item {i}", "price": float(i * 100)},
        )
        await storage.add_tracking(f"user_{i}", f"amazon:B0000000{i:02d}", float(i * 90))

    # Run 20 concurrent operations
    await asyncio.gather(*(add_dummy(i) for i in range(20)))

    with open(temp_storage_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        assert len(data["users"]) == 20
        assert len(data["products"]) == 20
        assert len(data["tracking"]) == 20


@pytest.mark.asyncio
async def test_price_history_capping(temp_storage_path: Path, monkeypatch):
    """Verify price history is capped at MAX_HISTORY_ENTRIES."""
    import config
    monkeypatch.setattr(config, "MAX_HISTORY_ENTRIES", 3)

    storage = StorageManager(file_path=temp_storage_path)
    await storage.initialize()

    product_key = "amazon:B0TESTHIST"

    # Add 5 different prices
    for p in [100.0, 95.0, 90.0, 85.0, 80.0]:
        await storage.upsert_product(product_key, {"price": p, "title": "Test Item"})

    history = await storage.get_price_history(product_key)
    assert len(history) == 3
    # Check that it retained the latest 3 prices
    assert [h["price"] for h in history] == [90.0, 85.0, 80.0]
