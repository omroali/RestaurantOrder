"""
utils.py

Shared helper functions used across the ordering pipeline:
  - text normalisation
  - quantity / size word parsing (with explicit-quantity tracking)
  - intent detection (affirmative, negative, correction, removal)

All functions operate on plain strings and have no external dependencies.
"""

import re
from typing import Optional, Tuple


# ---------------------------------------------------------------------------
# Quantity words understood by the parser
# Future improvement: extend with "a dozen", "half a dozen", ordinals, etc.
# ---------------------------------------------------------------------------
QUANTITY_WORDS: dict = {
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    " couple": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}

# Articles that resolve to qty=1 but are NOT an explicit count.
# "a coffee" and "one coffee" both mean qty=1, but only "one coffee" is an
# explicit statement of quantity; "a coffee" is just using the indefinite
# article.  The distinction matters when deciding whether to inherit the
# existing basket quantity during a correction.
_ARTICLE_WORDS: frozenset = frozenset({"a", "an"})

# ---------------------------------------------------------------------------
# Size words recognised near a menu item
# "regular" is intentionally omitted – it is handled as a Coke synonym so that
# "regular Coke" maps to the Coke menu item rather than being treated as a size.
# ---------------------------------------------------------------------------
SIZE_WORDS = {
    "small", "medium", "large", "grande", "venti", "xl", "extra large",
}

# ---------------------------------------------------------------------------
# Correction trigger words / phrases
# ---------------------------------------------------------------------------
_CORRECTION_TRIGGERS = [
    "actually", "wait", "no wait", "change that", "make that",
    "make it", "i meant", "i mean", "sorry", "scratch that",
    "cancel that", "change", "instead", "replace", "update",
    "remove", "switch", "swap",
]

# ---------------------------------------------------------------------------
# Affirmative / negative words for confirmation handling
# ---------------------------------------------------------------------------
_CONFIRM_WORDS = {
    "yes", "yeah", "yep", "yup", "correct", "right", "that's right",
    "thats right", "that is right", "sure", "ok", "okay", "perfect",
    "exactly", "confirmed", "sounds good", "looks good", "go ahead",
    "please", "definitely", "absolutely",
}

_DENY_WORDS = {
    "no", "nope", "nah", "wrong", "incorrect", "not right", "that's wrong",
    "thats wrong", "that is wrong", "not quite", "not exactly",
}

# ---------------------------------------------------------------------------
# Removal trigger patterns
# Matched against the *normalised* transcript (apostrophes already stripped,
# so "don't" becomes "don t").
# ---------------------------------------------------------------------------
_REMOVAL_PATTERNS: list = [
    re.compile(r"\bremove\b"),
    re.compile(r"\bdelete\b"),
    re.compile(r"\bdrop\b"),
    re.compile(r"\bforget\s+the\b"),
    re.compile(r"\bscratch\s+the\b"),
    re.compile(r"\btake\s+(?:off|out)\b"),
    re.compile(r"\bdon\s*t\s+want\b"),      # covers "don't want" & "don t want"
    re.compile(r"\bdo\s+not\s+want\b"),
    re.compile(r"\bno\s+longer\s+want\b"),
    re.compile(r"\bcancel\s+(?:the|my|a|an)\b"),  # "cancel the X" (not "cancel that")
]


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def normalize_text(text: str) -> str:
    """
    Lowercase, collapse punctuation to spaces, and strip extra whitespace.

    Example:
        "Can I get two Cheeseburgers, please?" -> "can i get two cheeseburgers please"
    """
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)   # replace punctuation with space
    text = re.sub(r"\s+", " ", text)        # collapse whitespace
    return text.strip()


def parse_quantity_with_explicitness(text: str) -> Tuple[Optional[int], bool]:
    """
    Return (quantity, is_explicit).

    is_explicit=True  when the quantity came from a genuine number word
                      ("one", "two", …, "ten") or a digit.
    is_explicit=False when the quantity came only from an article
                      ("a" / "an") or was not present at all.

    The distinction is used by apply_correction(): if the customer says
    "make the Coke a Diet Coke", the "a" is just an article (not explicit),
    so the existing quantity should be preserved.  But if they say
    "give me 1 coke" or "two cokes", the count is explicit and should
    override whatever is currently in the basket.

    Examples:
        "two cheeseburgers"  -> (2, True)
        "a large Diet Coke"  -> (1, False)   # article
        "3 cokes"            -> (3, True)
        "cheeseburger"       -> (None, False) # no quantity at all
    """
    for word in text.split():
        if word in _ARTICLE_WORDS:
            return (1, False)
        if word in QUANTITY_WORDS:
            return (QUANTITY_WORDS[word], True)
        if word.isdigit():
            return (int(word), True)
    return (None, False)


def parse_quantity(text: str) -> Optional[int]:
    """
    Return the first quantity found in *text* (word or digit), or None.

    Scans left-to-right so the leading quantity is always preferred.
    Use parse_quantity_with_explicitness() when you also need to know
    whether the quantity was explicit or inferred from an article.

    Examples:
        "two cheeseburgers" -> 2
        "3 cokes"           -> 3
        "cheeseburger"      -> None
    """
    qty, _ = parse_quantity_with_explicitness(text)
    return qty


def parse_size(text: str) -> Optional[str]:
    """
    Return the first size word found in *text*, or None.

    Multi-word sizes ("extra large") are checked before single words.

    Examples:
        "a large Diet Coke" -> "large"
        "small fries"       -> "small"
    """
    for size in sorted(SIZE_WORDS, key=len, reverse=True):
        if re.search(r"\b" + re.escape(size) + r"\b", text):
            return size
    return None


def is_affirmative(text: str) -> bool:
    """Return True if the text is a confirmation (yes, correct, etc.)."""
    normalized = normalize_text(text)
    for phrase in sorted(_CONFIRM_WORDS, key=len, reverse=True):
        if normalized == phrase or normalized.startswith(phrase + " "):
            return True
    return False


def is_negative(text: str) -> bool:
    """Return True if the text is a plain denial (no, wrong, etc.)."""
    normalized = normalize_text(text)
    for phrase in sorted(_DENY_WORDS, key=len, reverse=True):
        if normalized == phrase or normalized.startswith(phrase + " "):
            return True
    return False


def is_removal(text: str) -> bool:
    """
    Return True if the text is requesting removal of a basket item.

    Examples that return True:
        "remove the cheeseburger"
        "I don't want the fries"
        "take off the coke"
        "forget the coffee"
    """
    normalized = normalize_text(text)
    return any(p.search(normalized) for p in _REMOVAL_PATTERNS)


def is_correction(text: str) -> bool:
    """
    Return True if the text contains a correction trigger word/phrase.

    Note: plain "no" is treated as a denial, not a correction, so callers
    should check is_negative first.
    """
    normalized = normalize_text(text)
    for trigger in _CORRECTION_TRIGGERS:
        if re.search(r"\b" + re.escape(trigger) + r"\b", normalized):
            return True
    return False


def is_menu_inquiry(text: str) -> bool:
    """
    Return True if the user is asking about the menu.

    Examples that return True:
        "what's on the menu"
        "what do you have"
        "what can I order"
        "show me the menu"
        "tell me about the menu"
    """
    normalized = normalize_text(text)
    menu_triggers = [
        r"\bmenu\b",
        r"\bwhat\s+(?:do\s+)?you\s+have\b",
        r"\bwhat\s+can\s+i\s+(?:order|get)\b",
        r"\bwhat\s+(?:is\s+)?available\b",
        r"\bshow\s+(?:me\s+)?(?:the\s+)?menu\b",
        r"\btell\s+me\s+about\s+(?:the\s+)?menu\b",
        r"\boptions\b",
        r"\bitems\b",
    ]
    for trigger in menu_triggers:
        if re.search(trigger, normalized):
            return True
    return False


def is_halal_inquiry(text: str) -> bool:
    """
    Return True if the user is asking about halal.

    Examples that return True:
        "is it halal"
        "is everything halal"
        "do you have halal"
    """
    normalized = normalize_text(text)
    halal_triggers = [
        r"\bhalal\b",
    ]
    for trigger in halal_triggers:
        if re.search(trigger, normalized):
            return True
    return False


def format_menu_list(menu: list) -> str:
    """
    Format the menu data into a readable list for display.

    Parameters
    ----------
    menu : list
        Menu items as loaded from menu.json.

    Returns
    -------
    str
        Formatted menu string ready for TTS.
    """
    if not menu:
        return "I'm sorry, the menu is not available right now."

    # Group items by category
    categories = {}
    for item in menu:
        category = item.get("category", "Other")
        if category not in categories:
            categories[category] = []
        categories[category].append(item.get("name", "Unknown"))

    lines = ["Here's what we have available:"]
    category_names = {
        "burger": "Burgers",
        "side": "Sides",
        "drink": "Drinks",
        "hot_drink": "Hot Drinks",
        "dessert": "Desserts",
        "appetizer": "Appetizers",
    }

    for category_key in sorted(categories.keys()):
        category_label = category_names.get(category_key, category_key.title())
        lines.append(f"\n{category_label}:")
        for item_name in categories[category_key]:
            lines.append(f"  - {item_name}")

    return "\n".join(lines)
