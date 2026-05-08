"""
dialogue_manager.py

Orchestrates the full ordering conversation using a simple state machine.

States
------
  idle                – waiting to start
  listening           – waiting for speech (audio mode)
  transcribing        – ASR in progress
  extracting_order    – parser running
  clarifying          – asking for a required option
  summarising         – building confirmation text
  awaiting_confirmation – waiting for yes/no/correction
  correcting          – processing an in-flight correction
  confirmed           – order finalised; JSON produced

The manager exposes a single entry point:

    response, is_done = manager.process_input(user_text)

*response* is the robot's reply string.
*is_done* is True once the order is confirmed; the caller should print or
forward *response* (which contains the final JSON) and stop the loop.

No external dependencies are required.
Future improvement: replace the state dict with a proper FSM library or
integrate a full dialogue framework (e.g. Rasa, ConvLab) for multi-turn
intent tracking.
"""

import re
from typing import List, Optional, Tuple

from . import utils
from .models import OrderItem
from .order_parser import OrderParser
from .order_state import OrderState


# All valid states – kept here for documentation / introspection purposes.
DIALOGUE_STATES: List[str] = [
    "idle",
    "listening",
    "transcribing",
    "extracting_order",
    "clarifying",
    "summarising",
    "awaiting_confirmation",
    "correcting",
    "confirmed",
]
from typing import Any, Dict, List, Optional, Tuple
import re

from src import utils
from src.models import OrderItem
from src.order_state import OrderState
from src.order_parser import OrderParser


class DialogueManager:
    """
    Manages multiple ordering sessions, each associated with a unique table_id.

    Parameters
    ----------
    menu : list of dict
        Menu data as loaded from menu.json.

    Example
    -------
    >>> dm = DialogueManager(menu)
    >>> resp, done = dm.process_input("Two cheeseburgers and a large Diet Coke", "table_1")
    >>> print(resp)
    I have two cheeseburgers and one large Diet Coke. Is that correct?
    >>> resp, done = dm.process_input("Yes", "table_1")
    >>> print(done)
    True
    """

    def __init__(self, menu: List[dict]) -> None:
        self.menu = menu
        self.parser = OrderParser(menu)
        # Stores per-table state: {table_id: {"order_state": OrderState, "state": str, "pending_clarification": Optional[Tuple[OrderItem, List[str]]]}}
        self._tables: Dict[str, Dict[str, Any]] = {}

    def _get_table_state(self, table_id: str) -> Dict[str, Any]:
        """Retrieve or initialise the state for a given table_id."""
        if table_id not in self._tables:
            self._tables[table_id] = {
                "order_state": OrderState(self.menu),
                "state": "idle",
                "pending_clarification": None,
            }
        return self._tables[table_id]

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def process_input(self, text: str, table_id: str = "default") -> Tuple[str, bool]:
        """
        Process one turn of user input and return (robot_response, is_done).

        Parameters
        ----------
        text : str
            Raw text from the user (typed or ASR-transcribed).
        table_id : str
            The ID of the table for which the order is being processed.

        Returns
        -------
        response : str
            What the robot should say / display.
        is_done : bool
            True when the order has been confirmed; the response contains JSON.
        """
        text = text.strip()
        if not text:
            return ("I didn't catch that. Could you please repeat?", False)

        table_state = self._get_table_state(table_id)
        order_state = table_state["order_state"]
        current_state = table_state["state"]
        pending_clarification = table_state["pending_clarification"]

        # Route based on current state
        if current_state == "awaiting_confirmation":
            return self._handle_confirmation(text, table_id)

        if current_state == "clarifying":
            return self._handle_clarification(text, table_id)

        if current_state == "confirmed":
            return ("The order has already been confirmed. Thank you!", True)

        # When the user is actively correcting, route to the correction handler
        # so that apply_correction() is used (replacing items) rather than
        # add_items() (which would accumulate duplicates).
        if current_state == "correcting":
            return self._handle_correction(text, table_id)

        # idle / listening / extracting_order -> treat as a fresh order
        return self._handle_order_input(text, table_id)

    # ------------------------------------------------------------------
    # State handlers
    # ------------------------------------------------------------------

    def _handle_order_input(self, text: str, table_id: str) -> Tuple[str, bool]:
        """
        Process an initial order utterance or additional items added mid-session.
        """
        table_state = self._get_table_state(table_id)
        table_state["state"] = "extracting_order"
        items = self.parser.parse(text)

        if not items:
            table_state["state"] = "idle"
            return (
                "Sorry, I couldn't find any menu items in that. "
                "Could you try again?\n"
                "For example: \"I'd like a cheeseburger and a large Coke.\"",
                False,
            )

        table_state["order_state"].add_items(items)
        return self._build_response(table_id=table_id)

    def _handle_confirmation(self, text: str, table_id: str) -> Tuple[str, bool]:
        """
        Handle the user's response when the system is awaiting confirmation.

        Accepted responses:
          - Removal      → remove the named item and re-summarise
          - Affirmative  → confirm and return JSON
          - Correction   → update the basket and re-summarise
          - Negative     → ask what they'd like to change
          - Parseable    → treat as implicit correction/addition
          - Unclear      → ask again
        """
        table_state = self._get_table_state(table_id)
        order_state = table_state["order_state"]

        # Removal must be checked before correction: "remove" is also a
        # correction trigger, and we want item removal, not replacement.
        if utils.is_removal(text):
            return self._handle_removal(text, table_id)

        # Check for correction trigger (e.g. "actually make the Coke regular")
        if utils.is_correction(text):
            return self._handle_correction(text, table_id)

        if utils.is_affirmative(text):
            table_state["state"] = "confirmed"
            final_json = order_state.to_json()
            return (final_json, True)

        if utils.is_negative(text):
            table_state["state"] = "correcting"
            return ("I see! What would you like to change?", False)

        # Try to interpret as an implicit correction (user re-states an item)
        items = self.parser.parse(text)
        if items:
            return self._handle_correction(text, table_id)

        return (
            "Sorry, I didn't quite catch that. "
            "Please say 'yes' to confirm or describe what you'd like to change.",
            False,
        )

    def _handle_correction(self, text: str, table_id: str) -> Tuple[str, bool]:
        """
        Parse *text* for new/updated items and apply them to the basket.

        Processing order (each step short-circuits if it fires):
          1. Removal     – "remove X", "don't want X" etc.
          2. Increment   – "another X", "one more X", "N more X"
          3. Change-to   – "change X to Y" / "switch X to N Y"
                           (only the TARGET phrase after 'to' is parsed)
          4. General replacement / addition via apply_correction()
        """
        table_state = self._get_table_state(table_id)
        order_state = table_state["order_state"]
        table_state["state"] = "correcting"

        if utils.is_removal(text):
            return self._handle_removal(text, table_id)

        # Increment intent ("another coke", "two more fries")
        result = self._try_increment(text, table_id)
        if result is not None:
            return result

        # For "change X to Y" or "switch X to N Y", parse only the target
        parse_text = self._extract_correction_target(text) or text
        items = self.parser.parse(parse_text)

        if not items:
            return (
                "I'm not sure what you'd like to change. "
                "Could you describe the update? "
                "For example: \"change the Coke to a Diet Coke\".",
                False,
            )

        for item in items:
            order_state.apply_correction(item)

        return self._build_response(prefix="Updated. ", table_id=table_id)

    # ------------------------------------------------------------------
    # Increment helper
    # ------------------------------------------------------------------

    def _try_increment(self, text: str, table_id: str) -> Optional[Tuple[str, bool]]:
        """
        Detect increment requests and adjust the basket in-place.

        Recognised patterns (matched against normalised text):
          - "another X"         -> existing quantity + 1
          - "one more X"        -> existing quantity + 1
          - "N more X"          -> existing quantity + N
          - "more X" (no N)     -> existing quantity + 1

        If no matching pattern is found, returns None so the caller can
        fall through to the general correction logic.
        """
        table_state = self._get_table_state(table_id)
        order_state = table_state["order_state"]
        normalised = utils.normalize_text(text)
        increment = 0
        parse_text = normalised

        if re.search(r"\banother\b", normalised):
            # "another X" -> +1, substitute "another" with "a" for the parser
            increment = 1
            parse_text = re.sub(r"\banother\b", "a", normalised)

        elif re.search(r"\bmore\b", normalised):
            m = re.search(r"\b(\w+)\s+more\b", normalised)
            if m:
                word = m.group(1)
                n = utils.QUANTITY_WORDS.get(word) or (int(word) if word.isdigit() else 0)
                increment = n if n > 0 else 1
                # Remove "N more" from the text to leave just the item name
                parse_text = (normalised[: m.start()] + " " + normalised[m.end():]).strip()
            else:
                increment = 1
                parse_text = re.sub(r"\bmore\b", "", normalised).strip()

        if increment == 0:
            return None  # not an increment request

        items = self.parser.parse(parse_text)
        if not items:
            return None  # couldn't identify which item to increment

        target = items[0]

        # Look for an exact id match in the basket first, then a similar item
        found = next(
            (b for b in order_state.items if b.item_id == target.item_id),
            order_state._find_similar_item(target),
        )

        if found:
            order_state.update_item(found.item_id, quantity=found.quantity + increment)
        else:
            target.quantity = increment
            order_state.add_items([target])

        return self._build_response(prefix="Updated. ", table_id=table_id)

    # ------------------------------------------------------------------
    # Change-target extraction helper
    # ------------------------------------------------------------------

    def _extract_correction_target(self, text: str) -> Optional[str]:
        """
        For phrases like "change X to Y" or "switch X to N Y", extract just
        the TARGET state Y (the part after the last occurrence of ' to ').

        The extracted substring is returned only when it contains a
        recognisable menu item, so that innocent uses of the word 'to'
        (e.g. "I want to order a burger") are not misinterpreted.

        Returns None when no such pattern is found.

        Examples:
            "change 1 cheeseburger to 3 cheeseburgers" -> "3 cheeseburgers"
            "switch the coke to a diet coke"           -> "a diet coke"
            "make it a diet coke"                      -> None  (no 'to')
        """
        normalised = utils.normalize_text(text)
        # Split on " to " and take the last segment as the target
        for separator in (" to ", " into "):
            if separator in normalised:
                candidate = normalised.rsplit(separator, 1)[-1].strip()
                if candidate and self.parser._find_menu_item(candidate):
                    return candidate
        return None

    def _handle_removal(self, text: str, table_id: str) -> Tuple[str, bool]:
        """
        Remove the menu item(s) named in *text* from the basket.

        Uses the parser to identify which item(s) the user is referring to,
        then calls order_state.remove_item() for each one found.

        If no item is recognised, the robot lists the current basket contents
        so the user can clarify.
        """
        table_state = self._get_table_state(table_id)
        order_state = table_state["order_state"]
        table_state["state"] = "correcting"

        # Reuse the parser to find which menu item(s) are mentioned
        items = self.parser.parse(text)

        if not items:
            # Parser found no menu item – tell the user what's in the order
            if order_state.is_empty():
                table_state["state"] = "idle"
                return ("Your order is already empty. What would you like?", False)
            summary = order_state.generate_summary()
            return (
                f"Which item would you like to remove? "
                f"Your order currently has {summary}.",
                False,
            )

        removed: List[str] = []
        not_found: List[str] = []

        for item in items:
            if order_state.remove_item(item.item_id):
                removed.append(item.display_name)
            else:
                not_found.append(item.display_name)

        if not removed:
            names = ", ".join(not_found)
            return (
                f"I don't see {names} in your order. "
                "What would you like to remove?",
                False,
            )

        prefix = "Got it, I've removed the " + ", ".join(removed) + ". "

        if order_state.is_empty():
            table_state["state"] = "idle"
            return (
                prefix + "Your order is now empty. What would you like?",
                False,
            )

        return self._build_response(prefix=prefix, table_id=table_id)

    def _handle_clarification(self, text: str, table_id: str) -> Tuple[str, bool]:
        """
        Resolve an outstanding required-option question.

        Example: system asked "Would you like grilled or crispy?" for a
        Chicken Burger; user replies "grilled".
        """
        table_state = self._get_table_state(table_id)
        order_state = table_state["order_state"]
        pending_clarification = table_state["pending_clarification"]

        if pending_clarification is None:
            return self._build_response(table_id=table_id)

        item, missing_options = pending_clarification
        option_name = missing_options[0]

        menu_entry = next((m for m in self.menu if m["id"] == item.item_id), None)
        if menu_entry:
            allowed = menu_entry.get("allowed_options", {}).get(option_name, [])
            for value in allowed:
                if value.lower() in text.lower():
                    order_state.update_item(
                        item.item_id, options={option_name: value}
                    )
                    table_state["pending_clarification"] = None
                    return self._build_response(table_id=table_id)

            # User replied but we couldn't match any allowed value
            options_str = " or ".join(allowed) if allowed else option_name
            return (f"I'm sorry, could you choose between {options_str}?", False)

        # Fallback: unknown menu entry
        table_state["pending_clarification"] = None
        return self._build_response(table_id=table_id)

    # ------------------------------------------------------------------
    # Response builder
    # ------------------------------------------------------------------

    def _build_response(self, prefix: str = "", table_id: str = "default") -> Tuple[str, bool]:
        """
        Check for missing required options; if any remain, ask a clarification
        question.  Otherwise generate a confirmation prompt.
        """
        table_state = self._get_table_state(table_id)
        order_state = table_state["order_state"]

        missing = order_state.get_missing_required_options()

        if missing:
            item, missing_options = missing[0]
            option_name = missing_options[0]
            menu_entry = next((m for m in self.menu if m["id"] == item.item_id), None)

            allowed: List[str] = []
            if menu_entry:
                allowed = menu_entry.get("allowed_options", {}).get(option_name, [])

            table_state["pending_clarification"] = (item, missing_options)
            table_state["state"] = "clarifying"

            if allowed:
                options_str = " or ".join(allowed)
                return (
                    f"{prefix}For the {item.display_name}, "
                    f"would you like it {options_str}?",
                    False,
                )
            return (
                f"{prefix}What {option_name} would you like "
                f"for the {item.display_name}?",
                False,
            )

        # All required options filled → confirm
        summary = order_state.generate_summary()
        table_state["state"] = "awaiting_confirmation"
        return (f"{prefix}I have {summary}. Is that correct?", False)

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def get_final_json(self, table_id: str = "default") -> Optional[str]:
        """Return confirmed JSON, or None if the order is not yet confirmed."""
        table_state = self._get_table_state(table_id)
        order_state = table_state["order_state"]

        if table_state["state"] == "confirmed":
            return order_state.to_json()
        return None

    def reset(self, table_id: str = "default") -> None:
        """Reset the manager for a brand-new ordering session for a specific table."""
        table_state = self._get_table_state(table_id)
        table_state["order_state"].clear()
        table_state["state"] = "idle"
        table_state["pending_clarification"] = None
