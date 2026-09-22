"""Base classes and data models for product providers."""

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional


@dataclass(slots=True)
class ProductInfo:
    """Lightweight product data container (slots=True saves memory on Pi 2W)."""

    platform: str
    product_id: str
    title: str
    url: str
    price: float
    currency: str = "INR"
    available: bool = True
    last_checked: Optional[str] = None

    def __post_init__(self):
        if self.last_checked is None:
            self.last_checked = datetime.now(timezone.utc).isoformat()

    @property
    def product_key(self) -> str:
        """Globally unique key for storage, e.g. 'amazon:B09XXXXXXX'."""
        return f"{self.platform}:{self.product_id}"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON persistence."""
        return asdict(self)


class ProductProvider(ABC):
    """Abstract interface for e-commerce platforms."""

    @abstractmethod
    def supports(self, url: str) -> bool:
        """Return True if this provider can handle the given URL."""
        pass

    @abstractmethod
    def extract_id(self, url: str) -> Optional[str]:
        """Extract unique product identifier (e.g. ASIN for Amazon)."""
        pass

    @abstractmethod
    def canonical_url(self, url: str) -> str:
        """Return clean, canonical URL stripped of tracking/referral params."""
        pass

    @abstractmethod
    async def get_product(self, url: str) -> Optional[ProductInfo]:
        """Fetch current product details and price."""
        pass
