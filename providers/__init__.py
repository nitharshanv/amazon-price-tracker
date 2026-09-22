"""Provider registry and lookup."""

from typing import List, Optional

from providers.amazon import AmazonProvider
from providers.base import ProductInfo, ProductProvider
from providers.flipkart import FlipkartProvider

# Registered providers
PROVIDERS: List[ProductProvider] = [
    AmazonProvider(),
    FlipkartProvider(),
]


def get_provider(url: str) -> Optional[ProductProvider]:
    """Find a matching provider for the given URL."""
    for provider in PROVIDERS:
        if provider.supports(url):
            return provider
    return None


__all__ = ["ProductProvider", "ProductInfo", "AmazonProvider", "FlipkartProvider", "get_provider", "PROVIDERS"]
