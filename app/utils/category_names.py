"""Clave de identidad ortográfica compartida por persistencia y migración."""
import re
import unicodedata


def category_name_key(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).casefold()
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^\w]+", " ", text, flags=re.UNICODE).replace("_", " ").split())
