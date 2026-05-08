"""
order_parser.py

Deterministic, rule-based parser that turns a normalised transcript into a
list of OrderItem objects.

Algorithm (clause-based):
  1. Normalise the transcript.
  2. Split into clauses on conjunctions / commas.
  3. For each clause:
       a. Find the best matching menu item (longest pattern wins).
       b. Extract quantity, size, modifiers, and options from surrounding words.
       c. If no item is found but modifiers/size are present, attach them to
          the most recently seen item (handles "one without onions" after
          "two cheeseburgers").
  4. Return the collected OrderItems.

No ML or external libraries are required.
Future improvement: replace regex modifier extraction with a small NLP pipeline
or integrate spaCy PhraseMatcher for multi-language support.
"""

import re
from typing import Dict, List, Optional, Tuple

from . import utils
from .models import OrderItem


# ---------------------------------------------------------------------------
# Clause splitting
# Splits on:  ", and "  |  ", "  |  " and "  |  " also "  |  " plus "
# The (?:and\s+)? part swallows a trailing "and" after a comma so that
# "cheeseburgers, and a Coke" splits cleanly into two clauses.
# ---------------------------------------------------------------------------
_CLAUSE_SEP = re.compile(
    r"\s*,\s*(?:and\s+)?|\s+and\s+|\s+also\s+|\s+plus\s+",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Modifier extraction patterns.
# Each entry is (compiled_regex, format_string).
#
# For the optional second captured word we add a negative lookahead
# (?!_MOD_KW) so that the next modifier keyword is never consumed as part
# of the current modifier value.
# Example: "no onions extra cheese"
#   - "no" pattern grabs "onions" (stops before "extra")  -> "no onions"
#   - "extra" pattern grabs "cheese"                       -> "extra cheese"
# ---------------------------------------------------------------------------
_MOD_KW = r"(?:no|extra|without|hold|with)\b"   # shared lookahead

_MODIFIER_PATTERNS: List[Tuple[re.Pattern, str]] = [
    # "no onions"  /  "no ice"
    (re.compile(r"\bno\s+(\w+(?:\s+(?!" + _MOD_KW + r")\w+)?)"), "no {}"),
    # "without onions"
    (re.compile(r"\bwithout\s+(\w+(?:\s+(?!" + _MOD_KW + r")\w+)?)"), "no {}"),
    # "hold the onions"  /  "hold onions"
    (re.compile(r"\bhold(?:\s+the)?\s+(\w+(?:\s+(?!" + _MOD_KW + r")\w+)?)"), "no {}"),
    # "extra cheese"
    (re.compile(r"\bextra\s+(\w+(?:\s+(?!" + _MOD_KW + r")\w+)?)"), "extra {}"),
    # "with milk"  /  "with oat milk"
    # Negative lookahead prevents matching "with no …" or "with extra …"
    (re.compile(r"\bwith\s+(?!no\s|extra\s)(\w+(?:\s+(?!" + _MOD_KW + r")\w+)?)"), "with {}"),
]

# Words that must not appear in the captured modifier text.
_MOD_STOP: set = {
    "the", "a", "an", "please", "thanks", "thank", "you",
    "and", "or", "but", "any", "actually", "wait", "sorry",
    "change", "make", "instead", "switch", "swap",
}

# Opening phrases that add no ordering information.
_FILLER_PREFIXES = [
    "can i get", "i would like", "i want", "i ll have", "id like",
    "could i have", "give me", "can i have", "let me get", "ill have",
    "i d like", "may i have", "i will have", "i'd like",
]


# ---------------------------------------------------------------------------
# Public class
# ---------------------------------------------------------------------------

class OrderParser:
    """
    Parses a free-text transcript into a list of OrderItems.

    Usage:
        parser = OrderParser(menu)          # menu is a list of dicts from menu.json
        items  = parser.parse(transcript)   # returns List[OrderItem]
    """

    def __init__(self, menu: List[dict]) -> None:
        self.menu = menu
        # Pre-build pattern list once; sorted longest-first so "diet coke"
        # always matches before "coke".
        self._patterns: List[Tuple[str, dict]] = self._build_patterns()

    # ------------------------------------------------------------------
    # Primary public method
    # ------------------------------------------------------------------

    def parse(self, transcript: str) -> List[OrderItem]:
        """
        Parse *transcript* and return a list of extracted OrderItems.

        Items that are inferred (no direct menu match in the clause) are
        assigned confidence=0.8 to signal they may need review.
        """
        # Lowercase FIRST so the clause separator regex matches case-insensitively,
        # but keep punctuation (commas) intact so clauses split correctly.
        # Each clause is then individually normalised (punctuation removed) below.
        lowered = transcript.lower()
        clauses = self._split_into_clauses(lowered)

        items: List[OrderItem] = []
        last_menu_item: Optional[dict] = None

        for raw_clause in clauses:
            # Normalise each clause individually: remove punctuation, collapse spaces
            clause = re.sub(r"[^\w\s]", " ", raw_clause)
            clause = re.sub(r"\s+", " ", clause).strip()
            if not clause:
                continue

            # Strip common filler phrases at the start of a clause.
            clause = self._strip_filler(clause)
            if not clause:
                continue

            menu_item = self._find_menu_item(clause)

            if menu_item is not None:
                # ---- Clause contains a known menu item ----
                last_menu_item = menu_item
                qty_value, qty_explicit = utils.parse_quantity_with_explicitness(clause)
                quantity  = qty_value if qty_value is not None else 1
                size      = utils.parse_size(clause)
                modifiers = self._extract_modifiers(clause)
                options   = self._extract_options(clause, menu_item)

                items.append(OrderItem(
                    line_id=0,          # assigned later by OrderState
                    item_id=menu_item["id"],
                    display_name=menu_item["name"],
                    quantity=quantity,
                    size=size,
                    options=options,
                    modifiers=modifiers,
                    confidence=1.0,
                    explicit_quantity=qty_explicit,
                ))

            elif last_menu_item is not None:
                # ---- No item found – check for modifier / size that belong
                #      to the most recently mentioned item.
                #      Example: "one without onions" after "two cheeseburgers"
                modifiers = self._extract_modifiers(clause)
                size      = utils.parse_size(clause)

                if modifiers or size:
                    qty_value, qty_explicit = utils.parse_quantity_with_explicitness(clause)
                    quantity = qty_value if qty_value is not None else 1
                    items.append(OrderItem(
                        line_id=0,
                        item_id=last_menu_item["id"],
                        display_name=last_menu_item["name"],
                        quantity=quantity,
                        size=size,
                        options={},
                        modifiers=modifiers,
                        confidence=0.8,
                        explicit_quantity=qty_explicit,
                    ))
            # If neither condition is met, the clause is ignored (e.g. "please")

        return items

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_patterns(self) -> List[Tuple[str, dict]]:
        """
        Build a list of (pattern_string, menu_item) pairs.

        For each menu item we include:
          - the canonical name
          - every synonym
          - automatic simple plural for names that don't already end in 's'

        The list is sorted longest-first so that more specific patterns
        (e.g. "diet coke") are always tried before shorter ones ("coke").
        """
        patterns: List[Tuple[str, dict]] = []
        for item in self.menu:
            names = [item["name"].lower()] + [s.lower() for s in item.get("synonyms", [])]
            for name in names:
                patterns.append((name, item))
                if not name.endswith("s"):
                    patterns.append((name + "s", item))
        patterns.sort(key=lambda x: len(x[0]), reverse=True)
        return patterns

    def _split_into_clauses(self, text: str) -> List[str]:
        """Split normalised text into order clauses."""
        return _CLAUSE_SEP.split(text)

    def _strip_filler(self, clause: str) -> str:
        """Remove common ordering preambles from the start of a clause."""
        for phrase in sorted(_FILLER_PREFIXES, key=len, reverse=True):
            if clause.startswith(phrase):
                clause = clause[len(phrase):].strip()
                break
        return clause

    def _find_menu_item(self, text: str) -> Optional[dict]:
        """
        Return the menu item dict whose pattern matches *text*, preferring the
        longest (most specific) match.  Returns None if nothing matches.
        """
        for pattern, item in self._patterns:
            if re.search(r"\b" + re.escape(pattern) + r"\b", text):
                return item
        return None

    def _extract_modifiers(self, text: str) -> List[str]:
        """
        Extract modifier phrases from *text* using the defined patterns.

        Deduplicates results and filters stop words from captured groups.
        """
        modifiers: List[str] = []
        for regex, fmt in _MODIFIER_PATTERNS:
            for match in regex.finditer(text):
                captured = match.group(1).strip()
                cleaned  = self._clean_mod_capture(captured)
                if cleaned:
                    mod = fmt.format(cleaned)
                    if mod not in modifiers:
                        modifiers.append(mod)
        return modifiers

    def _clean_mod_capture(self, text: str) -> str:
        """
        Remove stop words and size words from a modifier capture group,
        then return at most 2 remaining words.

        Example: "onions please" -> "onions"
        """
        stop = _MOD_STOP | utils.SIZE_WORDS
        words = [w for w in text.split() if w not in stop]
        return " ".join(words[:2])

    def _extract_options(self, text: str, menu_item: dict) -> Dict[str, str]:
        """
        Match allowed option values for *menu_item* against *text*.

        Example: "grilled" found in text for a Chicken Burger
                 -> {"style": "grilled"}
        """
        options: Dict[str, str] = {}
        for option_name, values in menu_item.get("allowed_options", {}).items():
            for value in values:
                if re.search(r"\b" + re.escape(value.lower()) + r"\b", text):
                    options[option_name] = value
                    break   # first match wins per option
        return options
