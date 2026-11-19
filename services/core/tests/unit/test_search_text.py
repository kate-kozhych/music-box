from __future__ import annotations

import pytest

from musicbox_core.apps.catalog.search_text import album_search_text, normalise


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Björk", "bjork"),
        ("  Sigur   Rós ", "sigur ros"),
        ("MOTÖRHEAD", "motorhead"),
        ("Straße", "strasse"),
        ("ﬁve", "five"),
        ("", ""),
    ],
)
def test_normalise_lowercases_folds_accents_and_collapses_spaces(raw: str, expected: str) -> None:
    assert normalise(raw) == expected


def test_album_search_text_joins_title_and_credit() -> None:
    assert album_search_text("Ágætis byrjun", "Sigur Rós") == "agætis byrjun sigur ros"
