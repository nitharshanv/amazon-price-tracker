"""End-to-end integration test simulating complete product tracking lifecycle."""

from unittest.mock import AsyncMock, MagicMock
from pathlib import Path
import pytest
from providers.base import ProductInfo
from services.scheduler import PriceCheckerScheduler
from services.tracker import TrackerService
from storage import StorageManager


@pytest.mark.asyncio
async def test_full_user_tracking_lifecycle(tmp_path: Path, monkeypatch):
    """Simulate full lifecycle:

    1. User submits product link
    2. Bot queries provider and gets current price
    3. User sets target price
    4. Price drop occurs in background scheduled check
    5. Bot dispatches Telegram alert with product button
    6. User views /list and /history
    7. User removes product with /remove
    8. Storage prunes orphaned product
    """
    storage_path = tmp_path / "lifecycle_storage.json"
    storage = StorageManager(file_path=storage_path)
    await storage.initialize()

    tracker = TrackerService(storage=storage)
    user_id = 987654321

    # 1. Mock product data from Amazon
    initial_product = ProductInfo(
        platform="amazon",
        product_id="B09LIFETEST",
        title="Sony WH-1000XM5 Wireless Headphones",
        url="https://www.amazon.in/dp/B09LIFETEST",
        price=29999.0,
    )

    import providers
    mock_provider = MagicMock()
    mock_provider.supports.return_value = True
    mock_provider.get_product = AsyncMock(return_value=initial_product)
    monkeypatch.setattr(providers, "get_provider", lambda url: mock_provider)

    # 2. User sets target price: 25000
    success, msg, prod = await tracker.add_product_tracking(
        user_id=user_id,
        url="https://www.amazon.in/dp/B09LIFETEST",
        target_price=25000.0,
    )
    assert success is True
    assert prod.price == 29999.0

    # 3. User checks /list
    items = await tracker.get_user_items(user_id)
    assert len(items) == 1
    assert items[0]["product"]["title"] == "Sony WH-1000XM5 Wireless Headphones"
    assert items[0]["target_price"] == 25000.0

    # 4. Background Scheduler setup
    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock(return_value=True)
    scheduler = PriceCheckerScheduler(bot=mock_bot, storage=storage)

    # First check: Price is still 29999 -> No alert
    stats1 = await scheduler.check_all_prices()
    assert stats1["alerts_sent"] == 0
    assert mock_bot.send_message.call_count == 0

    # Second check: Price drops to 24499 (below target 25000) -> Alert triggered!
    discounted_product = ProductInfo(
        platform="amazon",
        product_id="B09LIFETEST",
        title="Sony WH-1000XM5 Wireless Headphones",
        url="https://www.amazon.in/dp/B09LIFETEST",
        price=24499.0,
    )
    mock_provider.get_product = AsyncMock(return_value=discounted_product)

    stats2 = await scheduler.check_all_prices()
    assert stats2["alerts_sent"] == 1
    assert mock_bot.send_message.call_count == 1

    # Verify alert message structure
    call_args = mock_bot.send_message.call_args[1]
    assert call_args["chat_id"] == user_id
    assert "₹24,499" in call_args["text"]
    assert "₹25,000" in call_args["text"]
    assert "PRICE ALERT" in call_args["text"]
    assert call_args["reply_markup"] is not None

    # Third check: Price remains 24499 -> No duplicate alert (anti-spam)
    mock_bot.send_message.reset_mock()
    stats3 = await scheduler.check_all_prices()
    assert stats3["alerts_sent"] == 0
    assert mock_bot.send_message.call_count == 0

    # 5. User checks /history
    success_h, _, h_data = await tracker.get_item_history(user_id, "1")
    assert success_h is True
    # Two price points: 29999 and 24499
    assert len(h_data["history"]) == 2
    assert h_data["history"][0]["price"] == 29999.0
    assert h_data["history"][1]["price"] == 24499.0

    # 6. User removes tracking with /remove 1
    success_rem, _ = await tracker.remove_user_item(user_id, "1")
    assert success_rem is True

    # 7. Check list is now empty and product is pruned
    remaining = await tracker.get_user_items(user_id)
    assert len(remaining) == 0

    unique_products = await storage.get_unique_tracked_products()
    assert len(unique_products) == 0
