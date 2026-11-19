from __future__ import annotations

import unicodedata


def normalise(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    folded = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(folded.casefold().split())


def album_search_text(title: str, artist_credit: str) -> str:
    return normalise(f"{title} {artist_credit}")
