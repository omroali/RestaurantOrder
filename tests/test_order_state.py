"""
test_order_state.py

Unit tests for OrderState.

Covers:
  - add_items assigns sequential line IDs
  - remove_item by id and by display name
  - update_item modifies individual fields
  - apply_correction replaces a similar existing item
  - apply_correction inherits size / quantity from replaced item
  - get_missing_required_options detects unfilled required options
  - generate_summary produces correct English strings
  - generate_summary groups consecutive same-item lines elegantly
  - to_json produces valid JSON with expected structure
  - clear resets the basket

Run with:
    pytest tests/test_order_state.py -v
"""

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.models import OrderItem
from src.order_state import OrderState, _pluralize, _qty_to_word

# ---------------------------------------------------------------------------
# Load the real menu
# ---------------------------------------------------------------------------
with open(os.path.join(_ROOT, "menu.json"), encoding="utf-8") as _f:
    MENU = json.load(_f)


# ---------------------------------------------------------------------------
# Helpers to build OrderItems quickly
# ---------------------------------------------------------------------------

def make_item(
    item_id, display_name, quantity=1, size=None,
    options=None, modifiers=None, explicit_quantity=False,
):
    """
    Build an OrderItem for tests.

    explicit_quantity=True  mirrors what the parser sets when the user said
    a real number word or digit.  Set it to True in tests that verify the
    correction respects an explicitly-stated quantity.
    explicit_quantity=False (default) mirrors an article ("a"/"an") or a
    quantity that was inferred by default; the correction handler will
    inherit the existing basket quantity instead.
    """
    return OrderItem(
        line_id=0,
        item_id=item_id,
        display_name=display_name,
        quantity=quantity,
        size=size,
        options=options or {},
        modifiers=modifiers or [],
        explicit_quantity=explicit_quantity,
    )


def fresh_state() -> OrderState:
    return OrderState(MENU)


# ===========================================================================
# add_items
# ===========================================================================

class TestAddItems:

    def test_line_ids_assigned_sequentially(self):
        state = fresh_state()
        state.add_items([
            make_item("cheeseburger", "Cheeseburger"),
            make_item("coke", "Coke"),
        ])
        assert state.items[0].line_id == 1
        assert state.items[1].line_id == 2

    def test_multiple_calls_increment_ids(self):
        state = fresh_state()
        state.add_items([make_item("cheeseburger", "Cheeseburger")])
        state.add_items([make_item("fries", "Fries")])
        assert state.items[1].line_id == 2

    def test_is_empty_before_add(self):
        assert fresh_state().is_empty()

    def test_not_empty_after_add(self):
        state = fresh_state()
        state.add_items([make_item("coke", "Coke")])
        assert not state.is_empty()


# ===========================================================================
# remove_item
# ===========================================================================

class TestRemoveItem:

    def test_remove_by_id(self):
        state = fresh_state()
        state.add_items([make_item("cheeseburger", "Cheeseburger")])
        result = state.remove_item("cheeseburger")
        assert result is True
        assert state.is_empty()

    def test_remove_by_display_name(self):
        state = fresh_state()
        state.add_items([make_item("coke", "Coke")])
        assert state.remove_item("Coke") is True
        assert state.is_empty()

    def test_remove_non_existent_returns_false(self):
        state = fresh_state()
        assert state.remove_item("pizza") is False

    def test_remove_first_matching_item(self):
        """If two lines match, only the first is removed."""
        state = fresh_state()
        state.add_items([
            make_item("cheeseburger", "Cheeseburger"),
            make_item("cheeseburger", "Cheeseburger", modifiers=["no onions"]),
        ])
        state.remove_item("cheeseburger")
        assert len(state.items) == 1
        assert "no onions" in state.items[0].modifiers


# ===========================================================================
# update_item
# ===========================================================================

class TestUpdateItem:

    def test_update_quantity(self):
        state = fresh_state()
        state.add_items([make_item("fries", "Fries", quantity=1)])
        state.update_item("fries", quantity=3)
        assert state.items[0].quantity == 3

    def test_update_size(self):
        state = fresh_state()
        state.add_items([make_item("coffee", "Coffee")])
        state.update_item("coffee", size="large")
        assert state.items[0].size == "large"

    def test_update_options_merges(self):
        state = fresh_state()
        state.add_items([make_item("chicken_burger", "Chicken Burger")])
        state.update_item("chicken_burger", options={"style": "grilled"})
        assert state.items[0].options["style"] == "grilled"

    def test_update_modifiers_replaces(self):
        state = fresh_state()
        state.add_items([make_item("cheeseburger", "Cheeseburger", modifiers=["no pickles"])])
        state.update_item("cheeseburger", modifiers=["no onions"])
        assert state.items[0].modifiers == ["no onions"]

    def test_update_non_existent_returns_false(self):
        state = fresh_state()
        assert state.update_item("pizza", quantity=2) is False

    def test_none_args_do_not_overwrite(self):
        state = fresh_state()
        state.add_items([make_item("fries", "Fries", quantity=2, size="large")])
        state.update_item("fries", quantity=3)  # size stays
        assert state.items[0].size == "large"


# ===========================================================================
# apply_correction
# ===========================================================================

class TestApplyCorrection:

    def test_exact_id_match_replaces_item(self):
        state = fresh_state()
        state.add_items([make_item("coke", "Coke", quantity=2)])
        # explicit_quantity=True because the caller is explicitly setting qty=3
        correction = make_item("coke", "Coke", quantity=3, explicit_quantity=True)
        state.apply_correction(correction)
        assert len(state.items) == 1
        assert state.items[0].quantity == 3

    def test_line_id_preserved_after_correction(self):
        state = fresh_state()
        state.add_items([make_item("coke", "Coke")])
        original_line_id = state.items[0].line_id
        state.apply_correction(make_item("coke", "Coke", quantity=5))
        assert state.items[0].line_id == original_line_id

    def test_similar_item_by_category_and_name(self):
        """Replacing 'Diet Coke' with 'Coke' via name overlap."""
        state = fresh_state()
        state.add_items([make_item("diet_coke", "Diet Coke", size="large")])
        correction = make_item("coke", "Coke")  # no size specified
        state.apply_correction(correction)
        assert len(state.items) == 1
        assert state.items[0].item_id == "coke"

    def test_size_inherited_from_replaced_item(self):
        """If correction has no size, carry over the existing item's size."""
        state = fresh_state()
        state.add_items([make_item("diet_coke", "Diet Coke", size="large")])
        correction = make_item("coke", "Coke")   # size=None
        state.apply_correction(correction)
        assert state.items[0].size == "large"

    def test_quantity_inherited_when_correction_defaults_to_one(self):
        state = fresh_state()
        state.add_items([make_item("coke", "Coke", quantity=3)])
        correction = make_item("diet_coke", "Diet Coke", quantity=1)
        state.apply_correction(correction)
        # quantity should be inherited from the original Coke (qty=3)
        assert state.items[0].quantity == 3

    def test_no_similar_item_adds_new(self):
        state = fresh_state()
        state.add_items([make_item("cheeseburger", "Cheeseburger")])
        state.apply_correction(make_item("water", "Water"))
        assert len(state.items) == 2


# ===========================================================================
# get_missing_required_options
# ===========================================================================

class TestMissingRequiredOptions:

    def test_detects_missing_style_for_chicken_burger(self):
        state = fresh_state()
        state.add_items([make_item("chicken_burger", "Chicken Burger")])
        missing = state.get_missing_required_options()
        assert len(missing) == 1
        item, opts = missing[0]
        assert item.item_id == "chicken_burger"
        assert "style" in opts

    def test_no_missing_when_option_supplied(self):
        state = fresh_state()
        state.add_items([
            make_item("chicken_burger", "Chicken Burger", options={"style": "grilled"})
        ])
        assert state.get_missing_required_options() == []

    def test_no_missing_for_items_without_required_options(self):
        state = fresh_state()
        state.add_items([make_item("cheeseburger", "Cheeseburger")])
        assert state.get_missing_required_options() == []

    def test_mixed_items_only_missing_reported(self):
        state = fresh_state()
        state.add_items([
            make_item("cheeseburger", "Cheeseburger"),
            make_item("chicken_burger", "Chicken Burger"),  # missing style
        ])
        missing = state.get_missing_required_options()
        assert len(missing) == 1
        assert missing[0][0].item_id == "chicken_burger"


# ===========================================================================
# generate_summary
# ===========================================================================

class TestGenerateSummary:

    def test_single_item(self):
        state = fresh_state()
        state.add_items([make_item("cheeseburger", "Cheeseburger")])
        assert state.generate_summary() == "one Cheeseburger"

    def test_plural_qty(self):
        state = fresh_state()
        state.add_items([make_item("cheeseburger", "Cheeseburger", quantity=2)])
        assert state.generate_summary() == "two Cheeseburgers"

    def test_size_appears_before_name(self):
        state = fresh_state()
        state.add_items([make_item("diet_coke", "Diet Coke", size="large")])
        assert state.generate_summary() == "one large Diet Coke"

    def test_modifier_displayed_as_without(self):
        state = fresh_state()
        state.add_items([
            make_item("cheeseburger", "Cheeseburger", modifiers=["no onions"])
        ])
        assert "without onions" in state.generate_summary()

    def test_consecutive_same_item_omits_name(self):
        """
        Two cheeseburger lines in a row: second line should read
        'one without onions', not 'one Cheeseburger without onions'.
        """
        state = fresh_state()
        state.add_items([
            make_item("cheeseburger", "Cheeseburger", quantity=2),
            make_item("cheeseburger", "Cheeseburger", quantity=1, modifiers=["no onions"]),
        ])
        summary = state.generate_summary()
        assert "two Cheeseburgers" in summary
        # 'without onions' should appear without repeating 'Cheeseburger' next to it
        assert "one without onions" in summary

    def test_full_spec_example_summary(self):
        state = fresh_state()
        state.add_items([
            make_item("cheeseburger", "Cheeseburger", quantity=2),
            make_item("cheeseburger", "Cheeseburger", quantity=1, modifiers=["no onions"]),
            make_item("diet_coke", "Diet Coke", quantity=1, size="large"),
        ])
        summary = state.generate_summary()
        assert "two Cheeseburgers" in summary
        assert "without onions" in summary
        assert "large Diet Coke" in summary

    def test_empty_basket(self):
        assert fresh_state().generate_summary() == "nothing"

    def test_oxford_comma(self):
        state = fresh_state()
        state.add_items([
            make_item("cheeseburger", "Cheeseburger"),
            make_item("fries", "Fries"),
            make_item("coke", "Coke"),
        ])
        summary = state.generate_summary()
        assert summary.endswith(", and one Coke")

    def test_extra_modifier_displayed(self):
        state = fresh_state()
        state.add_items([
            make_item("cheeseburger", "Cheeseburger", modifiers=["extra cheese"])
        ])
        assert "extra cheese" in state.generate_summary()


# ===========================================================================
# to_json
# ===========================================================================

class TestToJson:

    def test_returns_valid_json(self):
        state = fresh_state()
        state.add_items([make_item("cheeseburger", "Cheeseburger", quantity=1)])
        data = json.loads(state.to_json())
        assert isinstance(data, dict)

    def test_status_is_confirmed(self):
        state = fresh_state()
        state.add_items([make_item("coke", "Coke")])
        data = json.loads(state.to_json())
        assert data["status"] == "confirmed"

    def test_items_present_in_json(self):
        state = fresh_state()
        state.add_items([make_item("fries", "Fries", quantity=2)])
        data = json.loads(state.to_json())
        assert len(data["items"]) == 1
        assert data["items"][0]["item_id"] == "fries"
        assert data["items"][0]["quantity"] == 2

    def test_confirmation_text_included(self):
        state = fresh_state()
        state.add_items([make_item("water", "Water")])
        data = json.loads(state.to_json())
        assert data["confirmation_text"]


# ===========================================================================
# clear
# ===========================================================================

class TestClear:

    def test_clear_empties_basket(self):
        state = fresh_state()
        state.add_items([make_item("cheeseburger", "Cheeseburger")])
        state.clear()
        assert state.is_empty()

    def test_clear_resets_line_id_counter(self):
        state = fresh_state()
        state.add_items([make_item("cheeseburger", "Cheeseburger")])
        state.clear()
        state.add_items([make_item("coke", "Coke")])
        assert state.items[0].line_id == 1


# ===========================================================================
# Module-level helpers
# ===========================================================================

class TestHelpers:

    def test_qty_to_word(self):
        assert _qty_to_word(1)  == "one"
        assert _qty_to_word(5)  == "five"
        assert _qty_to_word(11) == "11"    # beyond dict → digit string

    def test_pluralize_default(self):
        assert _pluralize("Cheeseburger") == "Cheeseburgers"

    def test_pluralize_already_plural(self):
        assert _pluralize("Fries") == "Fries"

    def test_pluralize_y_ending(self):
        assert _pluralize("Berry") == "Berries"

    def test_pluralize_ey_ending(self):
        assert _pluralize("Turkey") == "Turkeys"
