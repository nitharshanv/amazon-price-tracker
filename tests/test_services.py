"""Unit tests for TrackerService and PriceCheckerScheduler."""

from unittest.mock import AsyncMock, MagicMock
from pathlib import Path
import pytest
from providers.base import ProductInfo
from services.scheduler import PriceCheckerScheduler
from services.tracker import TrackerService
from storage import StorageManager


@pytest.fixture
def temp_storage(tmp_path: Path) -> StorageManager:
    return StorageManager(file_path=tmp_path / "storage_test.json")


@pytest.mark.asyncio
async def test_tracker_service_add_and_list(temp_storage: StorageManager):
    service = TrackerService(storage=temp_storage)

    mock_product = ProductInfo(
        platform="amazon",
        product_id="B09TEST001",
        title="Sony WH-1000XM5",
        url="https://www.amazon.in/dp/B09TEST001",
        price=29999.0,
    )

    # Add product tracking with prefetched info
    success, msg, prod = await service.add_product_tracking(
        user_id="user1",
        url="https://www.amazon.in/dp/B09TEST001",
        target_price=25000.0,
        prefetched_product=mock_product,
    )
    assert success is True
    assert prod is not None
    assert prod.price == 29999.0

    # Retrieve user items
    items = await service.get_user_items("user1")
    assert len(items) == 1
    assert items[0]["target_price"] == 25000.0
    assert items[0]["product"]["title"] == "Sony WH-1000XM5"

    # Test history retrieval
    success_h, _, h_data = await service.get_item_history("user1", "1")
    assert success_h is True
    assert len(h_data["history"]) == 1
    assert h_data["history"][0]["price"] == 29999.0

    # Remove item by index
    success_r, _ = service.storage and await service.remove_user_item("user1", "1")
    assert success_r is True

    items_after = await service.get_user_items("user1")
    assert len(items_after) == 0


@pytest.mark.asyncio
async def test_scheduler_deduplication_and_alerting(temp_storage: StorageManager, monkeypatch):
    """Test that N users tracking 1 product only triggers 1 provider fetch,

    and sends alerts with anti-spam check.
    """
    await temp_storage.initialize()
    service = TrackerService(storage=temp_storage)

    product = ProductInfo(
        platform="amazon",
        product_id="B09DUPE001",
        title="Sony Headphones",
        url="https://www.amazon.in/dp/B09DUPE001",
        price=30000.0,
    )

    # 3 users track the SAME product with different targets
    # User 1 target: 25000
    # User 2 target: 28000
    # User 3 target: 20000
    await service.add_product_tracking("user1", product.url, 25000.0, prefetched_product=product)
    await service.add_product_tracking("user2", product.url, 28000.0, prefetched_product=product)
    await service.add_product_tracking("user3", product.url, 20000.0, prefetched_product=product)

    # Verify only 1 unique product in storage
    unique = await temp_storage.get_unique_tracked_products()
    assert len(unique) == 1

    # Mock provider fetch: Price drops to 24000
    fetch_call_count = 0

    async def mock_get_product(url):
        nonlocal fetch_call_count
        fetch_call_count += 1
        return ProductInfo(
            platform="amazon",
            product_id="B09DUPE001",
            title="Sony Headphones",
            url=url,
            price=24000.0,
        )

    import providers
    mock_provider = MagicMock()
    mock_provider.get_product = mock_get_product
    monkeypatch.setattr(providers, "get_provider", lambda url: mock_provider)

    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock(return_value=True)

    scheduler = PriceCheckerScheduler(bot=mock_bot, storage=temp_storage)

    # RUN 1: Price drops to 24000
    # Price changed: All 3 users get notified of the price drop!
    stats = await scheduler.check_all_prices()

    # Deduplication check: Provider was called only ONCE for all 3 users!
    assert fetch_call_count == 1
    assert stats["products_checked"] == 1
    assert stats["alerts_sent"] == 3
    assert mock_bot.send_message.call_count == 3

    # RUN 2: Anti-spam check. Price is still 24000.
    # Price did NOT change: 0 new alerts should be sent!
    mock_bot.send_message.reset_mock()
    stats2 = await scheduler.check_all_prices()
    assert stats2["alerts_sent"] == 0
    assert mock_bot.send_message.call_count == 0
