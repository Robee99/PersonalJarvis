"""compare_prices walks the store search pages and ranks the item per kilo.

Owner's test 2026-10-25: "scrape multiple grocery stores and tell me the most
affordable prices for items (example 1kg chicken)". The page texts below are
the shapes BigBasket, Blinkit and Zepto showed that day.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from jarvis.brain.price_compare import (
    STORES,
    ComparePricesTool,
    item_words,
    offers_in,
    page_text,
    search_term,
)
from jarvis.core.protocols import ToolResult

BLINKIT = """My Cart
Showing results for "chicken"
9 MINS
TWM Boneless Chicken Breast
450 g
₹249
ADD
9 MINS
Meatigo Chicken Whole (Skin On)
1 kg
₹400
ADD
24% OFF
9 MINS
Truchicken Boneless Chicken Breast
2 x 400 g
₹413
₹550
ADD
9 MINS
MasterChow Oyster Sauce
220 g
₹170
ADD
"""

ZEPTO = """Showing results for "chicken"
ADD
₹126
₹185
Relish Chicken Curry Cut Without Skin
1 pack (450 g)
ADD
₹144
₹164
Deli Chic Chicken Liver
1 pack (500 g)
ADD
"""

BIGBASKET = """No results found for chicken 1kg, instead showing results for chicken
Shop by
Fresh Chicken
Frozen Chicken
Chicken Variants
Breast
Curry Cut
Drumsticks & Leg
Boneless
Fillets
Price
Brands
Add
₹11 OFF
10 MINS
Meatigo
Chicken Curry Cut With Skin
3.9
1008 Ratings
450 g - (12-16 pcs)
₹109.00
₹120.00
Add
"""


def test_the_item_words_drop_the_pack_size() -> None:
    assert item_words("1kg chicken") == ["chicken"]
    assert search_term("2 x 500 g Amul butter") == "amul butter"


def test_a_page_yields_each_matching_card_with_its_price_per_kilo() -> None:
    offers = {o.name: o for o in offers_in("blinkit", BLINKIT, "1kg chicken")}

    assert set(offers) == {
        "TWM Boneless Chicken Breast",
        "Meatigo Chicken Whole (Skin On)",
        "Truchicken Boneless Chicken Breast",
    }
    whole = offers["Meatigo Chicken Whole (Skin On)"]
    assert (whole.price, whole.per_unit, whole.unit) == (400.0, 400.0, "kg")
    # The selling price, not the struck-out MRP; a two-pack counts both packs.
    assert offers["Truchicken Boneless Chicken Breast"].price == 413.0
    assert offers["Truchicken Boneless Chicken Breast"].per_unit == 516.25


def test_a_price_before_the_name_and_by_products_are_handled() -> None:
    offers = offers_in("zepto", ZEPTO, "chicken")

    assert [(o.name, o.price) for o in offers] == [("Relish Chicken Curry Cut Without Skin", 126.0)]


def test_filters_discount_badges_and_ratings_never_become_products() -> None:
    offers = offers_in("bigbasket", BIGBASKET, "1kg chicken")

    assert [(o.name, o.price, o.per_unit) for o in offers] == [
        ("Chicken Curry Cut With Skin", 109.0, 242.22)
    ]


def test_the_evaluate_result_is_read_out_of_the_markdown() -> None:
    raw = '### Result\n"line one\\nline two"\n### Ran Playwright code\n```js\n...\n```'

    assert page_text(raw) == "line one\nline two"


@dataclass
class _Browser:
    pages: dict[str, str]
    opened: list[str] = field(default_factory=list)
    name: str = "playwright/browser"

    async def execute(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        del ctx
        if "url" in args:
            self.opened.append(args["url"])
            return ToolResult(success=True, output="### Page\n- Page URL: " + args["url"])
        store = next(s for s in STORES if s in self.opened[-1])
        text = self.pages.get(store, "")
        return ToolResult(success=True, output="### Result\n" + json.dumps(text))


async def test_the_tool_walks_every_store_and_ranks_cheapest_per_kilo_first(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr("jarvis.brain.price_compare._SETTLE_S", 0)
    browser = _Browser({"blinkit": BLINKIT, "zepto": ZEPTO, "bigbasket": BIGBASKET})
    tool = ComparePricesTool(
        {"playwright/browser_navigate": browser, "playwright/browser_evaluate": browser}
    )

    result = await tool.execute({"item": "1kg chicken"}, None)

    assert result.success
    assert len(browser.opened) == len(STORES)
    assert all("q=chicken" in u or "query=chicken" in u for u in browser.opened)
    rows = [line for line in result.output.splitlines() if line.startswith("- ")]
    assert rows[0].startswith("- bigbasket: Chicken Curry Cut With Skin")
    assert "Rs 242/kg" in rows[0]


async def test_without_the_browser_it_says_what_to_turn_on() -> None:
    result = await ComparePricesTool({}).execute({"item": "1kg chicken"}, None)

    assert not result.success
    assert "Playwright" in (result.error or "")


async def test_a_store_with_nothing_matching_is_named_not_hidden(monkeypatch: Any) -> None:
    monkeypatch.setattr("jarvis.brain.price_compare._SETTLE_S", 0)
    browser = _Browser({"blinkit": BLINKIT})
    tool = ComparePricesTool(
        {"playwright/browser_navigate": browser, "playwright/browser_evaluate": browser}
    )

    result = await tool.execute({"item": "chicken", "stores": ["blinkit", "zepto"]}, None)

    assert "blinkit: Meatigo Chicken Whole" in result.output
    assert "zepto: not checked (no matching products" in result.output
