"""
models.py

Core data structures for the restaurant ordering system.
Uses Python dataclasses for simplicity and readability.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class OrderItem:
    """
    Represents a single line in the customer's order.

    Attributes:
        line_id:      Sequential ID assigned when the item enters the basket.
        item_id:      Canonical menu item identifier (e.g. "cheeseburger").
        display_name: Human-readable name shown in confirmations.
        quantity:     Number of this item ordered.
        size:         Optional size modifier ("small", "medium", "large", …).
        options:      Chosen values for required/allowed options
                      e.g. {"style": "grilled"} for a chicken burger.
        modifiers:    Free-text modifications e.g. ["no onions", "extra cheese"].
        confidence:   Parser confidence in the extraction (0.0–1.0).
                      Items inferred without an explicit menu match get 0.8.
    """

    line_id: int
    item_id: str
    display_name: str
    quantity: int = 1
    size: Optional[str] = None
    options: Dict[str, str] = field(default_factory=dict)
    modifiers: List[str] = field(default_factory=list)
    confidence: float = 1.0
    # Internal flag set by the parser: True when a number word or digit was
    # found in the transcript (e.g. "two", "3"), False when the quantity was
    # inferred from an article ("a", "an") or defaulted to 1.
    # Used by apply_correction() to decide whether to inherit the existing
    # basket item's quantity or to use the newly parsed quantity.
    explicit_quantity: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "line_id": self.line_id,
            "item_id": self.item_id,
            "display_name": self.display_name,
            "quantity": self.quantity,
            "size": self.size,
            "options": self.options,
            "modifiers": self.modifiers,
            "confidence": self.confidence,
        }


@dataclass
class Order:
    """
    Represents the complete, finalised customer order.

    Attributes:
        items:             All confirmed line items.
        status:            Lifecycle state: "pending" | "confirmed" | "cancelled".
        confirmation_text: Human-readable summary text produced at confirmation.
    """

    items: List[OrderItem] = field(default_factory=list)
    status: str = "pending"
    confirmation_text: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "items": [item.to_dict() for item in self.items],
            "status": self.status,
            "confirmation_text": self.confirmation_text,
        }
