"""Flipkart (flipkart.com) product provider."""

import json
import logging
import re
from typing import Optional
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup

from config import DEFAULT_USER_AGENT, REQUEST_TIMEOUT
from providers.base import ProductInfo, ProductProvider
from utils.formatter import clean_title, parse_price_text

logger = logging.getLogger(__name__)

PID_QUERY_REGEX = re.compile(r"[?&]pid=([A-Za-z0-9]+)", re.IGNORECASE)
ITM_REGEX = re.compile(r"/p/(itm[a-zA-Z0-9]+)", re.IGNORECASE)


class FlipkartProvider(ProductProvider):
    """Provider for Flipkart India products."""

    def supports(self, url: str) -> bool:
        """Check if URL is a Flipkart URL."""
        lower = url.lower()
        return "flipkart.com" in lower or "dl.flipkart.com" in lower or "fkrt.it" in lower

    def extract_id(self, url: str) -> Optional[str]:
        """Extract unique Flipkart product identifier (PID or ITM code)."""
        # Prefer pid query param if present
        pid_match = PID_QUERY_REGEX.search(url)
        if pid_match:
            return pid_match.group(1)

        # Fallback to itm code in path
        itm_match = ITM_REGEX.search(url)
        if itm_match:
            return itm_match.group(1)

        return None

    def canonical_url(self, url: str) -> str:
        """Strip affiliate and tracking query params from Flipkart URL."""
        parsed = urlparse(url)
        # Keep path and only pid if present in query
        qs = parse_qs(parsed.query)
        clean_query = f"pid={qs['pid'][0]}" if "pid" in qs else ""
        return f"https://www.flipkart.com{parsed.path}" + (f"?{clean_query}" if clean_query else "")

    async def get_product(self, url: str) -> Optional[ProductInfo]:
        """Fetch product details from Flipkart."""
        resolved_url = await self._resolve_redirects(url)
        product_id = self.extract_id(resolved_url)
        if not product_id:
            logger.warning("Could not extract product ID from Flipkart URL: %s", url)
            return None

        canonical = self.canonical_url(resolved_url)
        return await self._fetch_via_http(product_id, canonical)

    async def _resolve_redirects(self, url: str) -> str:
        """Resolve shortened or mobile app links."""
        if not any(d in url.lower() for d in ("dl.flipkart.com", "fkrt.it")):
            return url

        headers = {"User-Agent": DEFAULT_USER_AGENT}
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
                resp = await client.head(url, headers=headers)
                return str(resp.url)
        except Exception:
            return url

    async def _fetch_via_http(self, product_id: str, canonical_url: str) -> Optional[ProductInfo]:
        """Scrape product details from Flipkart with realistic headers."""
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
                    logger.warning("Flipkart HTTP request failed with status %s", resp.status_code)
                    return None

                html = resp.text

            # Parse with built-in html.parser (saves RAM on Pi 2W)
            soup = BeautifulSoup(html, "html.parser")

            # 1. Parse Title
            title = None
            for sel in ("h1.VU-ZEz", "span.B_NuCI", "h1._6EBuvT", "h1"):
                elem = soup.select_one(sel)
                if elem and elem.get_text(strip=True):
                    title = elem.get_text(strip=True)
                    break

            if not title:
                og_title = soup.find("meta", property="og:title")
                if og_title and og_title.get("content"):
                    title = og_title["content"].strip()

            # 2. Check Availability
            available = True
            for text_pattern in ("currently out of stock", "sold out", "item is out of stock"):
                if text_pattern in html.lower():
                    available = False
                    break

            # 3. Parse Price
            price: Optional[float] = None

            # Strategy A: Flipkart price CSS selectors
            for sel in (
                "div.Nx9daj",
                "div._30jeq3._16J7Pf",
                "div._30jeq3",
                "div._25b18c div._30jeq3",
                "div.hl05eU div._30jeq3",
            ):
                elem = soup.select_one(sel)
                if elem:
                    p = parse_price_text(elem.get_text())
                    if p:
                        price = p
                        break

            # Strategy B: JSON-LD
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

            # Strategy C: Regex match
            if price is None:
                matches = re.findall(r"₹\s*([0-9,]+)", html)
                for m in matches:
                    p = parse_price_text(m)
                    if p and p > 10:
                        price = p
                        break

            if title and price is not None:
                return ProductInfo(
                    platform="flipkart",
                    product_id=product_id,
                    title=clean_title(title),
                    url=canonical_url,
                    price=price,
                    currency="INR",
                    available=available,
                )

            logger.warning(
                "Incomplete Flipkart product parsed (title=%s, price=%s) for %s",
                title is not None,
                price is not None,
                canonical_url,
            )
            return None

        except Exception as e:
            logger.error("Error scraping Flipkart product %s: %s", product_id, e)
            return None
