"""Text helpers. Python sorts by code point, which puts «І», «Є», «Ї», «Ґ» before «А»."""

UK_ALPHABET = "абвгґдеєжзиіїйклмнопрстуфхцчшщьюя"
_RANK = {ch: i for i, ch in enumerate(UK_ALPHABET)}


def uk_sort_key(text):
    """Ukrainian alphabetical order; other characters (digits, Latin, punctuation) sort after letters."""
    return [(_RANK[ch], "") if ch in _RANK else (len(_RANK), ch) for ch in (text or "").lower().replace("'", "")]
