import re
import unicodedata
from difflib import SequenceMatcher

import pandas as pd


def normalize_text(value) -> str:
    if pd.isna(value):
        return ""

    text = str(value).strip().upper()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(
        char
        for char in text
        if not unicodedata.combining(char)
    )
    text = re.sub(r"[^A-Z0-9]+", " ", text)

    return re.sub(r"\s+", " ", text).strip()


def text_similarity(first, second) -> float:
    first_normalized = normalize_text(first)
    second_normalized = normalize_text(second)

    if not first_normalized or not second_normalized:
        return 0.0

    return round(
        SequenceMatcher(
            None,
            first_normalized,
            second_normalized,
        ).ratio() * 100,
        2,
    )
