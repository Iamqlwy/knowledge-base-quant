import re

# Pre-compiled regex cache for word_boundary_match — avoids re.compile() on every call
_ASCII_WB_CACHE: dict[str, re.Pattern] = {}


def word_boundary_match(entity_name: str, text: str) -> bool:
    """Check if entity_name appears as a whole word/phrase in text.

    For pure ASCII entity names: Latin word-boundary matching (prevents
    partial matches like "Sea" matching inside "Search").
    For CJK/mixed entity names: substring matching, because CJK text
    has no spaces between words and CJK characters are content, not boundaries.
    """
    name_lower = entity_name.lower()
    if entity_name.isascii():
        pattern = _ASCII_WB_CACHE.get(name_lower)
        if pattern is None:
            name = re.escape(name_lower)
            pattern = re.compile(r"(?<![a-zA-Z0-9])" + name + r"(?![a-zA-Z0-9])")
            _ASCII_WB_CACHE[name_lower] = pattern
            if len(_ASCII_WB_CACHE) > 4096:
                _ASCII_WB_CACHE.pop(next(iter(_ASCII_WB_CACHE)))
        return bool(pattern.search(text))
    return name_lower in text.lower()

def truncate(text: str, max_length: int = 2000) -> str:
    if len(text) <= max_length:
        return text
    return text[:max_length] + "..."
