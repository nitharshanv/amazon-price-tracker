"""Amazon India (amazon.in) product provider.

Supports:
1. Amazon Creators API / LWA (when credentials configured)
2. Resilient HTTP scraper fallback with realistic browser headers & JSON-LD parsing
"""

import json
import logging
import re
from typing import Any, Dict, Optional

import httpx
from bs4 import BeautifulSoup

from config import (
    AMAZON_CLIENT_ID,
    AMAZON_CLIENT_SECRET,
    AMAZON_REFRESH_TOKEN,
    DEFAULT_USER_AGENT,
    REQUEST_TIMEOUT,
)
from providers.base import ProductInfo, ProductProvider
from utils.formatter import clean_title, parse_price_text

logger = logging.getLogger(__name__)

ASIN_REGEX = re.compile(
    r"(?:/dp/|/gp/product/|/gp/aw/d/|/d/|/product/|[?&]asin=)([A-Z0-9]{10})",
    re.IGNORECASE,
)


class AmazonProvider(ProductProvider):
    """Provider for Amazon India products."""

    def __init__(self):
        self._lwa_access_token: Optional[str] = None
        self._token_expiry: float = 0.0

    def supports(self, url: str) -> bool:
        """Check if URL belongs to Amazon India or Amazon shortener."""
        lower = url.lower()
        return any(
            domain in lower
            for domain in ("amazon.in", "amzn.in", "amzn.to", "amzn.eu")
        )

    def extract_id(self, url: str) -> Optional[str]:
        """Extract 10-character Amazon ASIN."""
        match = ASIN_REGEX.search(url)
        if match:
            return match.group(1).upper()
        return None

    def canonical_url(self, url: str) -> str:
        """Generate canonical Amazon.in URL."""
        asin = self.extract_id(url)
        if asin:
            return f"https://www.amazon.in/dp/{asin}"
        return url

    async def get_product(self, url: str) -> Optional[ProductInfo]:
        """Retrieve product info using Creators API or HTTP scraper."""
        # Follow redirects for shortened links (e.g. amzn.to / amzn.in)
        resolved_url = await self._resolve_redirects(url)
        asin = self.extract_id(resolved_url)
        if not asin:
            logger.warning("Could not extract ASIN from URL: %s", url)
            return None

        canonical = f"https://www.amazon.in/dp/{asin}"

        # 1. Attempt Creators API if credentials are provided
        if AMAZON_CLIENT_ID and AMAZON_CLIENT_SECRET and AMAZON_REFRESH_TOKEN:
            try:
                prod = await self._fetch_creators_api(asin, canonical)
                if prod:
                    return prod
            except Exception as e:
                logger.warning("Amazon Creators API failed: %s. Falling back to HTTP fetch.", e)

        # 2. Resilient HTTP scraper fallback
        return await self._fetch_via_http(asin, canonical)

    async def _resolve_redirects(self, url: str) -> str:
        """Resolve shortened URL to destination URL."""
        if not any(d in url.lower() for d in ("amzn.in", "amzn.to", "amzn.eu")):
            return url

        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        try:
            # Amazon CDN returns 404 to HEAD on amzn.in/d/..., but follows 301/302 on GET
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
                resp = await client.get(url, headers=headers)
                return str(resp.url)
        except Exception:
            return url

    async def _fetch_creators_api(self, asin: str, canonical_url: str) -> Optional[ProductInfo]:
        """Fetch item data from Amazon Creators API / LWA."""
        # Exchange refresh token if needed
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            token_resp = await client.post(
                "https://api.amazon.com/auth/o2/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": AMAZON_REFRESH_TOKEN,
                    "client_id": AMAZON_CLIENT_ID,
                    "client_secret": AMAZON_CLIENT_SECRET,
                },
            )
            if token_resp.status_code != 200:
                logger.error("LWA Token exchange failed: %s", token_resp.text)
                return None

            token_data = token_resp.json()
            access_token = token_data.get("access_token")

            # Request product info from Creators API endpoint
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            }
            payload = {
                "ItemIds": [asin],
                "Resources": [
                    "ItemInfo.Title",
                    "Offers.Listings.Price",
                    "Offers.Listings.Availability.Message",
                ],
            }
            api_resp = await client.post(
                "https://api.amazon.com/creators/v1/items",
                headers=headers,
                json=payload,
            )
            if api_resp.status_code == 200:
                data = api_resp.json()
                items = data.get("ItemsResult", {}).get("Items", [])
                if items:
                    item = items[0]
                    title = item.get("ItemInfo", {}).get("Title", {}).get("DisplayValue", "")
                    price_val = None
                    listings = item.get("Offers", {}).get("Listings", [])
                    if listings:
                        price_val = listings[0].get("Price", {}).get("Amount")

                    if title and price_val is not None:
                        return ProductInfo(
                            platform="amazon",
                            product_id=asin,
                            title=clean_title(title),
                            url=canonical_url,
                            price=float(price_val),
                            currency="INR",
                            available=True,
                        )
        return None

    async def _fetch_via_http(self, asin: str, canonical_url: str) -> Optional[ProductInfo]:
        """Scrape product details from Amazon.in with realistic headers."""
        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
            "Sec-Ch-Ua": '"Chromium";v="126", "Not?A_Brand";v="24"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Linux"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        }

        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
                resp = await client.get(canonical_url, headers=headers)
                if resp.status_code != 200:
                    logger.warning("Amazon HTTP request failed with status %s", resp.status_code)
                    return None

                html = resp.text

            # Parse HTML with built-in html.parser (low RAM consumption on Pi 2W)
            soup = BeautifulSoup(html, "html.parser")

            # 1. Parse Title
            title = None
            title_tag = soup.find(id="productTitle")
            if title_tag:
                title = title_tag.get_text(strip=True)
            if not title:
                og_title = soup.find("meta", property="og:title")
                if og_title and og_title.get("content"):
                    title = og_title["content"].strip()
            if not title and soup.title:
                title = soup.title.string.strip() if soup.title.string else None
                if title and ":" in title:
                    title = title.split(":", 1)[-1].strip()

            # 2. Check Availability
            available = True
            avail_elem = soup.find(id="availability")
            if avail_elem:
                avail_text = avail_elem.get_text().lower()
                if "currently unavailable" in avail_text or "out of stock" in avail_text:
                    available = False

            # 3. Parse Price
            price: Optional[float] = None

            # Strategy A: Offscreen price element in priceToPay or apexPriceToPay
            for selector in (
                "span.priceToPay span.a-offscreen",
                "span.apexPriceToPay span.a-offscreen",
                "#corePrice_feature_div span.a-offscreen",
                "#corePriceDisplay_desktop_feature_div span.a-offscreen",
                "span.a-price span.a-offscreen",
                "#priceblock_ourprice",
                "#priceblock_dealprice",
            ):
                elem = soup.select_one(selector)
                if elem:
                    p = parse_price_text(elem.get_text())
                    if p:
                        price = p
                        break

            # Strategy B: JSON-LD Schema
            if price is None:
                for script in soup.find_all("script", type="application/ld+json"):
                    try:
                        data = json.loads(script.string or "")
                        if isinstance(data, dict):
                            offers = data.get("offers")
                            if isinstance(offers, dict) and "price" in offers:
                                p = parse_price_text(str(offers["price"]))
                                if p:
                                    price = p
                                    break
                            elif isinstance(offers, list) and offers:
                                p = parse_price_text(str(offers[0].get("price")))
                                if p:
                                    price = p
                                    break
                    except Exception:
                        continue

            # Strategy C: Regex search for Indian rupee in price blocks
            if price is None:
                matches = re.findall(r"₹\s*([0-9,]+(?:\.[0-9]{1,2})?)", html)
                for m in matches:
                    p = parse_price_text(m)
                    if p and p > 10:  # Avoid matching nominal values
                        price = p
                        break

            if title and price is not None:
                return ProductInfo(
                    platform="amazon",
                    product_id=asin,
                    title=clean_title(title),
                    url=canonical_url,
                    price=price,
                    currency="INR",
                    available=available,
                )

            logger.warning(
                "Incomplete Amazon product parsed (title=%s, price=%s) for %s",
                title is not None,
                price is not None,
                canonical_url,
            )
            return None

        except Exception as e:
            logger.error("Error scraping Amazon product %s: %s", asin, e)
            return None
