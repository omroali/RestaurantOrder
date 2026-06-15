"""
menu_dialogue.py  –  Dialogue manager for pre-set menu ordering.

Handles:
  - Interactive browsing: ask about menus, condiments before ordering
  - Menu selection by number or name (robust to ASR errors)
  - Condiment detection (explicit mention → skip asking)
  - Condiment offer (if not mentioned)
  - Order confirmation
"""

import json
import os
import re
from typing import Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG  = os.path.dirname(_HERE)

CONDIMENTS = ["tomato ketchup", "yellow mustard"]


class MenuDialogue:
    """State machine for menu-based ordering with interactive browsing."""

    def __init__(self):
        self.menus = self._load_menus()
        self.reset()

    def reset(self):
        self._state = "greeting"       # greeting | browsing | condiments | confirming | done
        self._selected_menu = None
        self._condiments = []
        self._declined_condiments = False
        self._has_shown_menu = False
        self._has_shown_condiments = False

    # ── Public API ────────────────────────────────────────────────────

    def process(self, text: str) -> Tuple[str, bool]:
        """Process one user utterance. Returns (robot_response, is_done)."""
        text = text.strip()

        if self._state == "greeting":
            return self._handle_greeting(text)

        if self._state == "browsing":
            return self._handle_browsing(text)

        if self._state == "condiments":
            return self._handle_condiments(text)

        if self._state == "confirming":
            return self._handle_confirmation(text)

        return ("Your order has already been confirmed.", True)

    def get_order(self) -> dict:
        """Return the confirmed order as a dict."""
        return {
            "menu": self._selected_menu,
            "condiments": self._condiments,
            "items_to_prepare": self._selected_menu["items"] if self._selected_menu else [],
        }

    # ── State handlers ────────────────────────────────────────────────

    def _handle_greeting(self, text: str) -> Tuple[str, bool]:
        self._state = "browsing"
        return (
            "Hello and welcome! I'm your waiter today. "
            "Feel free to ask what's on the menu, "
            "or tell me which menu you'd like.",
            False,
        )

    def _handle_browsing(self, text: str) -> Tuple[str, bool]:
        """Handle customer queries while browsing the menu."""

        # ── "What's on menu 4?" ──────────────────────────────────────
        menu = self._find_menu_in_query(text)
        if menu is not None:
            return (self._describe_menu(menu), False)

        # ── "What's on the menu?" ────────────────────────────────────
        if self._is_menu_list_inquiry(text):
            if self._has_shown_menu:
                return ("I already told you the menus. Which one would you like?", False)
            self._has_shown_menu = True
            return (self._menu_list_text(), False)

        # ── "What condiments do you have?" ───────────────────────────
        if self._is_condiment_inquiry(text):
            if self._has_shown_condiments:
                return ("As I mentioned, we have tomato ketchup and yellow mustard. Would you like either?", False)
            self._has_shown_condiments = True
            return (
                "We have tomato ketchup and yellow mustard. "
                "You can add them to any menu.",
                False,
            )

        # ── Menu selection ───────────────────────────────────────────
        menu = self._find_menu(text)
        if menu is not None:
            self._selected_menu = menu

            mentioned = self._extract_condiments(text)
            if mentioned:
                self._condiments = mentioned
                self._state = "confirming"
                return (self._confirmation_text(), False)

            if self._has_no_condiments(text):
                self._declined_condiments = True
                self._state = "confirming"
                return (self._confirmation_text(), False)

            self._state = "condiments"
            return (
                f"Great choice! Would you like any condiments with your "
                f"{menu['name']}? We have tomato ketchup and yellow mustard.",
                False,
            )

        # ── Didn't understand ────────────────────────────────────────
        return (
            "I'm not sure what you mean. You can ask 'what's on the menu?', "
            "'what's on menu 4?', 'what condiments do you have?', "
            "or just tell me which menu you'd like.",
            False,
        )

    def _handle_condiments(self, text: str) -> Tuple[str, bool]:
        # Allow going back to browsing for menu queries
        if self._is_menu_list_inquiry(text) or self._is_condiment_inquiry(text):
            return self._handle_browsing(text)

        if self._has_no_condiments(text):
            self._declined_condiments = True
            self._state = "confirming"
            return (self._confirmation_text(), False)

        condiments = self._extract_condiments(text)
        if condiments:
            self._condiments = condiments
            self._state = "confirming"
            return (self._confirmation_text(), False)

        return (
            "Sorry, I didn't catch that. Would you like tomato ketchup, "
            "yellow mustard, both, or neither?",
            False,
        )

    def _handle_confirmation(self, text: str) -> Tuple[str, bool]:
        if self._is_yes(text):
            self._state = "done"
            return ("Thank you. Your order will be ready shortly.", True)
        if self._is_no(text):
            self.reset()
            return ("No problem. Let's start again. What would you like?", False)
        return ("Sorry, was that a yes or no? Is your order correct?", False)

    # ── Query detectors ──────────────────────────────────────────────

    def _is_menu_list_inquiry(self, text: str) -> bool:
        """Detect 'what's on the menu?', 'what do you have?', etc."""
        lower = text.lower()
        patterns = [
            r"what.+(?:on|in)\s+(?:the\s+)?menu",
            r"what\s+(?:do\s+)?you\s+have",
            r"what\s+(?:can|should)\s+i\s+(?:order|get|have)",
            r"(?:show|tell|give)\s+(?:me\s+)?(?:the\s+)?menu",
            r"what\s+(?:are\s+)?(?:the\s+)?options",
            r"what\s+(?:is\s+)?available",
            r"list\s+(?:the\s+)?menu",
        ]
        return any(re.search(p, lower) for p in patterns)

    def _is_condiment_inquiry(self, text: str) -> bool:
        """Detect 'what condiments do you have?', 'do you have ketchup?', etc."""
        lower = text.lower()
        patterns = [
            r"what\s+(?:condiments|sauces|sauce|toppings|extras)",
            r"(?:do\s+)?you\s+have\s+(?:condiments|sauces|ketchup|mustard|sauce)",
            r"(?:got|have)\s+(?:any\s+)?(?:condiments|sauces|ketchup|mustard)",
            r"tell\s+(?:me\s+)?about\s+(?:the\s+)?(?:condiments|sauces)",
            r"what\s+(?:kind\s+of\s+)?(?:sauces|condiments)",
        ]
        return any(re.search(p, lower) for p in patterns)

    # ── Helpers ───────────────────────────────────────────────────────

    def _find_menu_in_query(self, text: str) -> Optional[dict]:
        """Detect 'what's on menu 4?' style queries and return the menu.

        Matches: 'what's on menu 4', 'tell me about menu two',
        'describe menu 3', 'what is menu 5', 'what does menu 4 have'.
        """
        lower = text.lower()

        # Prefix patterns for menu inquiry
        inquiry_prefixes = [
            r"what(?:'s|\s+is)?\s+(?:on|in|about)\s+",
            r"tell\s+(?:me\s+)?about\s+",
            r"describe\s+",
            r"what\s+does\s+",
            r"what\s+do\s+you\s+have\s+(?:on|in|for)\s+",
        ]
        prefix = r"(?:" + "|".join(inquiry_prefixes) + r")"

        # "<inquiry> menu 4"
        m = re.search(prefix + r"(?:the\s+)?menu\s*(\d)", lower)
        if m:
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(self.menus):
                return self.menus[idx]

        # "<inquiry> menu four"
        word_to_num = {
            'one': 1, 'won': 1, 'first': 1,
            'two': 2, 'to': 2, 'too': 2, 'second': 2,
            'three': 3, 'third': 3, 'tree': 3,
            'four': 4, 'for': 4, 'fourth': 4, 'fore': 4,
            'five': 5, 'fifth': 5, 'fight': 5,
        }
        m = re.search(prefix + r"(?:the\s+)?menu\s+(\w+)", lower)
        if m:
            num = word_to_num.get(m.group(1))
            if num and num <= len(self.menus):
                return self.menus[num - 1]

        # "what does menu 4 have?"
        m = re.search(r"menu\s*(\d)", lower)
        if m:
            # Only if it looks like a query (has what/tell/describe nearby)
            if re.search(r"what|tell|describe|explain|show", lower):
                idx = int(m.group(1)) - 1
                if 0 <= idx < len(self.menus):
                    return self.menus[idx]

        return None

    def _describe_menu(self, menu: dict) -> str:
        """Return a description of a single menu."""
        name = menu.get("name", "This menu")
        desc = menu.get("description", "")
        items = menu.get("items", [])
        item_list = ", ".join(items)
        return f"{name} includes {desc}."

    def _find_menu(self, text: str) -> Optional[dict]:
        """Find menu by number, word, or item name in text.

        Handles 'menu 2', 'menu two', 'number 2', 'item 5',
        'option 3', 'the second one', '5', 'five', item names,

        Also handles common ASR misrecognitions of 'menu':
        'many you', 'men you', 'me and you', 'man you',
        'me new', 'meant to', 'main you', 'venom'.
        """
        lower = text.lower()

        word_to_num = {
            'one': 1, 'won': 1, '1st': 1, 'first': 1,
            'run': 1, 'done': 1, 'fun': 1, 'wan': 1,
            'two': 2, 'to': 2, 'too': 2, '2nd': 2, 'second': 2,
            'do': 2, 'who': 2, 'chew': 2,
            'three': 3, '3rd': 3, 'third': 3,
            'tree': 3, 'free': 3, 'see': 3, 'spree': 3,
            'four': 4, 'for': 4, '4th': 4, 'fourth': 4,
            'fore': 4, 'floor': 4, 'door': 4, 'poor': 4,
            'five': 5, '5th': 5, 'fifth': 5,
            'fight': 5, 'file': 5, 'fine': 5, 'find': 5,
            'fire': 5, 'fly': 5, 'hive': 5, 'fife': 5,
        }

        _menu_words = [
            'menu', 'menus',
            'many you', 'men you', 'me and you', 'man you',
            'me new', 'meant to', 'main you', 'venom',
        ]
        _prefix_words = _menu_words + ['number', 'item', 'option', 'choice']
        prefix_re = r'(?:' + '|'.join(_prefix_words) + r')'

        # "<prefix> 2", "<prefix>2"
        m = re.search(prefix_re + r'\s*(\d)', lower)
        if m:
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(self.menus):
                return self.menus[idx]

        # "<prefix> two", "<prefix> to", etc.
        m = re.search(prefix_re + r'\s+(\w+)', lower)
        if m:
            num = word_to_num.get(m.group(1))
            if num and num <= len(self.menus):
                return self.menus[num - 1]

        # Ordinal stand-alone: "the second one", "the fifth", etc.
        ordinal_words = '|'.join(word_to_num.keys())
        ordinal_re = r'(?:the\s+)?(' + ordinal_words + r')(?:\s+(?:one|menu|item|option|choice))?'
        m = re.search(ordinal_re, lower)
        if m:
            num = word_to_num.get(m.group(1))
            if num and num <= len(self.menus):
                return self.menus[num - 1]

        # Bare digit or number word in short utterances
        words = lower.split()
        if len(words) <= 4:
            for w in words:
                if w.isdigit():
                    idx = int(w) - 1
                    if 0 <= idx < len(self.menus):
                        return self.menus[idx]
                num = word_to_num.get(w)
                if num and num <= len(self.menus):
                    return self.menus[num - 1]

        # Match by item name
        for menu in self.menus:
            for item in menu["items"]:
                if item.lower() in lower:
                    return menu

        return None

    def _extract_condiments(self, text: str) -> List[str]:
        """Extract condiments from text.  Robust to common ASR errors."""
        lower = text.lower()
        found = []

        ketchup_words = [
            'tomato ketchup', 'ketchup', 'tomato sauce', 'red sauce',
            'catch up', 'catchup', 'ketch up', 'cats up', 'catch app',
            'ketch app', 'catch a', 'ketch a',
        ]
        if any(w in lower for w in ketchup_words):
            if 'no ketchup' not in lower and 'without ketchup' not in lower:
                found.append(CONDIMENTS[0])

        mustard_words = [
            'yellow mustard', 'mustard', 'american mustard',
            'mustered', 'muster', 'must art', 'mustered', 'musterd',
            'must herd',
        ]
        if any(w in lower for w in mustard_words):
            if 'no mustard' not in lower and 'without mustard' not in lower:
                found.append(CONDIMENTS[1])

        both_words = [
            'both', 'all of them', 'everything', 'all',
            'boat', 'bowl',
        ]
        if any(w in lower for w in both_words):
            found = CONDIMENTS[:]

        return found

    def _has_no_condiments(self, text: str) -> bool:
        """Check if user explicitly declined condiments."""
        lower = text.lower()
        no_phrases = [
            "no condiments", "no sauce", "no sauces", "no thanks", "none",
            "without condiments", "without sauce",
            "no ketchup", "no mustard",
            "neither", "just the menu", "that's all", "thats all",
            "plain", "no extras", "nothing else", "that's it", "thats it",
            "no thank you", "i'm good", "im good",
            "know thanks", "know thank you", "now thanks",
            "known", "noon", "nun",
            "needa", "needer",
            "nope", "nah",
            "know ketchup", "know mustard",
            "know sauce", "know condiments",
        ]
        return any(p in lower for p in no_phrases)

    def _is_yes(self, text: str) -> bool:
        lower = text.lower()
        yes_words = [
            "yes", "yeah", "correct", "yep", "yup",
            "that's right", "thats right", "exactly",
            "sure", "ok", "okay", "fine", "good",
            "perfect", "right", "yes please",
            "years", "use", "uses", "yours",
            "yea", "ya", "year", "ye", "yay",
            "yet", "yip", "yap",
            "shore", "shoe", "sure thing",
            "okey", "okey dokey", "okie",
            "collect", "connect",
        ]
        return any(w in lower for w in yes_words)

    def _is_no(self, text: str) -> bool:
        lower = text.lower()
        no_words = [
            "no", "nope", "wrong", "incorrect", "not correct",
            "not right", "that's wrong", "thats wrong",
            "try again", "start over", "restart",
            "know", "now", "note", "not", "gnaw",
            "nop", "hope", "rope",
            "long", "rong", "wong",
            "in correct", "and correct", "in collect",
            "nah", "na", "naw",
        ]
        return any(w in lower for w in no_words)

    def _menu_list_text(self) -> str:
        lines = ["Here are our menus:"]
        for i, m in enumerate(self.menus, 1):
            lines.append(f"Menu {i}: {m['description']}.")
        lines.append("Which would you like?")
        return " ".join(lines)

    def _confirmation_text(self) -> str:
        menu_name = self._selected_menu["name"]
        if self._condiments:
            c_list = " and ".join(self._condiments)
            return f"So that's {menu_name} with {c_list}. Is that correct?"
        elif self._declined_condiments:
            return f"So that's {menu_name} with no condiments. Is that correct?"
        else:
            return f"So that's {menu_name}. Is that correct?"

    @staticmethod
    def _load_menus():
        path = os.path.join(_PKG, "menus.json")
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
        return []
