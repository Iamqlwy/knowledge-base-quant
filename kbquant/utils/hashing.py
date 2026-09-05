import hashlib
import re


def normalize_text(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text


def compute_content_hash(title: str, body: str) -> str:
    normalized = normalize_text(title) + " " + normalize_text(body)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
