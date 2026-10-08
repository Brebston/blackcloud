"""Перевірка, що рядок — це один емоджі (а не довільний текст, HTML чи керівні символи)."""

import unicodedata

MAX_CODEPOINTS = 10
JOINERS = {0x200D, 0xFE0F}  # ZWJ (👨‍👩‍👧) і селектор варіанта емоджі
EXTRA_SYMBOLS = {0x00A9, 0x00AE, 0x203C, 0x2049, 0x2122, 0x2139, 0x3030, 0x303D, 0x3297, 0x3299}


def _is_pictograph(cp: int, ch: str) -> bool:
    if 0x1F000 <= cp <= 0x1FAFF:  # основні блоки емоджі (включно з новими, яких ще немає в unicodedata)
        return True
    if 0x2190 <= cp <= 0x2BFF and unicodedata.category(ch) == "So":  # ☀️ ✅ ❤️ ⭐ тощо
        return True
    return cp in EXTRA_SYMBOLS


def is_single_emoji(value) -> bool:
    if not isinstance(value, str) or not value or len(value) > MAX_CODEPOINTS:
        return False
    pictographs = 0
    for ch in value:
        cp = ord(ch)
        if cp in JOINERS or 0x1F3FB <= cp <= 0x1F3FF or 0xE0020 <= cp <= 0xE007F:
            continue  # з'єднувачі, відтінки шкіри, теги прапорів
        if _is_pictograph(cp, ch):
            pictographs += 1
            continue
        return False  # літери, цифри, пробіли, HTML, керівні символи — заборонено
    return pictographs >= 1
