"""
order_state.py

Manages the in-progress basket of ordered items.

Responsibilities:
  - Maintain a sequential list of OrderItems (the "basket").
  - Assign unique line IDs to items as they are added.
  - Support removing, updating, and correcting items.
  - Check required options against the menu schema.
  - Generate a human-readable summary string.
  - Serialise the confirmed order to JSON.

No external dependencies are required.
"""

import json
from typing import Dict, List, Optional, Tuple

from .models import Order, OrderItem


# ---------------------------------------------------------------------------
# Module-level helpers for summary generation
# ---------------------------------------------------------------------------

_QTY_WORDS: Dict[int, str] = {
    1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
    6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten",
}


def _qty_to_word(qty: int) -> str:
    """Return the English word for *qty*, or the digit string for qty > 10."""
    return _QTY_WORDS.get(qty, str(qty))


def _pluralize(name: str) -> str:
    """
    Very simple English pluralisation for menu item names.

    Rules applied (in order):
      1. Already ends in 's'  ->  leave unchanged  (Fries -> Fries)
      2. Ends in 'ey'         ->  add 's'           (Turkey -> Turkeys)
      3. Ends in 'y'          ->  replace with 'ies'
      4. Default              ->  add 's'

    Future improvement: use inflect library for accurate pluralisation.
    """
    lower = name.lower()
    if lower.endswith("s"):
        return name
    if lower.endswith("ey"):
        return name + "s"
    if lower.endswith("y"):
        return name[:-1] + "ies"
    return name + "s"


def _format_modifier_for_display(mod: str) -> str:
    """
    Convert internal modifier notation to display-friendly text.

    Examples:
        "no onions"    -> "without onions"
        "extra cheese" -> "extra cheese"   (unchanged)
        "with milk"    -> "with milk"      (unchanged)
    """
    if mod.startswith("no "):
        return "without " + mod[3:]
    return mod


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class OrderState:
    """
    Maintains the current basket and provides all mutation / query methods.

    Usage:
        state = OrderState(menu)
        state.add_items(parser.parse(transcript))
        print(state.generate_summary())
        state.to_json()     # final confirmed JSON
    """

    def __init__(self, menu: List[dict]) -> None:
        self.menu = menu
        self._menu_map: Dict[str, dict] = {item["id"]: item for item in menu}
        self.items: List[OrderItem] = []
        self._next_line_id: int = 1

    # ------------------------------------------------------------------
    # Basket mutations
    # ------------------------------------------------------------------

    def add_items(self, items: List[OrderItem]) -> None:
        """Append *items* to the basket, assigning sequential line IDs."""
        for item in items:
            item.line_id = self._next_line_id
            self._next_line_id += 1
            self.items.append(item)

    def remove_item(self, item_name_or_id: str) -> bool:
        """
        Remove the first basket item matching *item_name_or_id* by id or
        display name (case-insensitive).

        Returns True if an item was removed, False if nothing matched.
        """
        needle = item_name_or_id.lower()
        for i, item in enumerate(self.items):
            if item.item_id == item_name_or_id or item.display_name.lower() == needle:
                self.items.pop(i)
                return True
        return False

    def update_item(
        self,
        item_name_or_id: str,
        quantity: Optional[int] = None,
        size: Optional[str] = None,
        options: Optional[dict] = None,
        modifiers: Optional[List[str]] = None,
    ) -> bool:
        """
        Update fields on the first matching basket item.

        Only non-None arguments are applied; existing values are preserved.
        Returns True if an item was found and updated.
        """
        needle = item_name_or_id.lower()
        for item in self.items:
            if item.item_id == item_name_or_id or item.display_name.lower() == needle:
                if quantity is not None:
                    item.quantity = quantity
                if size is not None:
                    item.size = size
                if options is not None:
                    item.options.update(options)
                if modifiers is not None:
                    item.modifiers = modifiers
                return True
        return False

    def apply_correction(self, new_item: OrderItem) -> None:
        """
        Apply a corrected OrderItem to the basket.

        Strategy:
          1. Find the most similar existing item (exact id match first, then
             same-category items with the most name-word overlap).
          2. If found, replace it in-place, preserving its line_id, and
             inheriting size / quantity from the original if the correction
             did not specify them.
          3. If no similar item is found, add it as a new basket line.

        Example:
            Basket has Diet Coke; correction is Coke (regular).
            "Coke" and "Diet Coke" share the word "coke" and the "drink"
            category, so Diet Coke is replaced with Coke, size preserved.
        """
        similar = self._find_similar_item(new_item)
        if similar is not None:
            # Inherit size from existing item if the correction didn't mention one
            if new_item.size is None:
                new_item.size = similar.size
            # Inherit quantity only when the speaker used an article ("a"/"an")
            # or gave no quantity at all.  If they said an explicit number word
            # or digit, that count takes priority over whatever is in the basket.
            if not new_item.explicit_quantity:
                new_item.quantity = similar.quantity
            # Preserve the original line_id for stable display ordering
            new_item.line_id = similar.line_id
            idx = self.items.index(similar)
            self.items[idx] = new_item
        else:
            self.add_items([new_item])

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_missing_required_options(self) -> List[Tuple[OrderItem, List[str]]]:
        """
        Return a list of (item, missing_option_names) for items that have
        required options not yet filled in.

        Used by the dialogue manager to generate clarification questions.
        """
        missing = []
        for item in self.items:
            menu_entry = self._menu_map.get(item.item_id)
            if menu_entry is None:
                continue
            required = menu_entry.get("required_options", [])
            unset = [opt for opt in required if opt not in item.options]
            if unset:
                missing.append((item, unset))
        return missing

    def generate_summary(self) -> str:
        """
        Build a natural-language summary of the current basket.

        If two consecutive items share the same item_id, the second item's
        display name is omitted to produce output like:
            "two cheeseburgers, one without onions, and one large Diet Coke"
        rather than the more verbose:
            "two cheeseburgers, one cheeseburger without onions, …"

        Returns "nothing" if the basket is empty.
        """
        if not self.items:
            return "nothing"

        parts: List[str] = []
        prev_item_id: Optional[str] = None

        for item in self.items:
            qty_word  = _qty_to_word(item.quantity)
            size_str  = (item.size + " ") if item.size else ""

            # Convert internal modifiers to display form
            mod_parts = [_format_modifier_for_display(m) for m in item.modifiers]
            mod_str   = (" " + " and ".join(mod_parts)) if mod_parts else ""

            if item.item_id == prev_item_id and item.modifiers:
                # Same item as previous line → omit the name for brevity
                parts.append(f"{qty_word}{mod_str}")
            else:
                name = item.display_name
                if item.quantity > 1:
                    name = _pluralize(name)
                parts.append(f"{qty_word} {size_str}{name}{mod_str}".strip())

            prev_item_id = item.item_id

        # Join with Oxford-style comma
        if len(parts) == 1:
            return parts[0]
        return ", ".join(parts[:-1]) + ", and " + parts[-1]

    def to_json(self) -> str:
        """
        Serialise the basket to a formatted JSON string.

        The returned JSON includes every OrderItem's fields plus top-level
        order metadata (status, confirmation_text).
        """
        order = Order(
            items=self.items,
            status="confirmed",
            confirmation_text=f"Order confirmed: {self.generate_summary()}",
        )
        return json.dumps(order.to_dict(), indent=2)

    def is_empty(self) -> bool:
        """Return True if the basket contains no items."""
        return len(self.items) == 0

    def clear(self) -> None:
        """Reset the basket for a fresh order."""
        self.items.clear()
        self._next_line_id = 1

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _find_similar_item(self, new_item: OrderItem) -> Optional[OrderItem]:
        """
        Return the existing basket item most similar to *new_item*.

        Similarity is judged in two passes:
          Pass 1 – exact item_id match (the user is explicitly modifying the
                   same menu item).
          Pass 2 – same category, highest count of shared name words
                   (e.g. "Coke" shares "coke" with "Diet Coke").

        Returns None if no suitable match is found.
        """
        # Pass 1: exact match
        for existing in self.items:
            if existing.item_id == new_item.item_id:
                return existing

        # Pass 2: same-category name overlap
        new_entry = self._menu_map.get(new_item.item_id)
        if new_entry is None:
            return None

        new_category  = new_entry.get("category", "")
        new_words     = set(new_item.display_name.lower().split())

        best_score: int = 0
        best_match: Optional[OrderItem] = None

        for existing in self.items:
            existing_entry = self._menu_map.get(existing.item_id)
            if existing_entry is None:
                continue
            if existing_entry.get("category") != new_category:
                continue
            overlap = len(new_words & set(existing.display_name.lower().split()))
            if overlap > best_score:
                best_score = overlap
                best_match = existing

        return best_match
