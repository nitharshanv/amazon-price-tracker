"""URL extraction and normalization utilities."""

import re
from typing import List

# Regular expression to extract URLs from message text
URL_REGEX = re.compile(
    r"(https?://(?:www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b[-a-zA-Z0-9()@:%_\+.~#?&//=]*)",
    re.IGNORECASE,
)


def extract_urls(text: str) -> List[str]:
    """Extract all HTTP/HTTPS URLs found in a string."""
    if not text:
        return []
    return URL_REGEX.findall(text)
