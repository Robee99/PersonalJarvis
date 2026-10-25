"""Compare one item's price across online grocery stores, in the visible browser.

Owner's test 2026-10-25: "scrape multiple grocery stores and tell me the most
affordable prices for items (example 1kg chicken)". Gemma 4 E4B could open a
store but not finish the job: it typed into a "Search bar" the page did not
have, asked for a one-level snapshot of a 13,000-pixel page, and the turn ended
on "I will try the next site". Store pages render prices in JavaScript, so a
plain HTTP fetch sees none.

This tool does the walk itself with the Playwright MCP server the turn already
has: for each store it opens the store's own search page in the visible
browser, reads the page text, and picks out product, pack size and price. The
model gets one table, cheapest per kilo or litre first.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote_plus

from jarvis.core.protocols import ExecutionContext, RiskTier, Tool, ToolResult

log = logging.getLogger(__name__)

COMPARE_PRICES = "compare_prices"

#: Store search pages, checked live 2026-10-25 from India: each lists product,
#: pack size and price in its page text without a sign-in.
STORES: dict[str, str] = {
    "bigbasket": "https://www.bigbasket.com/ps/?q={q}",
    "blinkit": "https://blinkit.com/s/?q={q}",
    "zepto": "https://www.zeptonow.com/search?query={q}",
}

#: Seconds a store page gets to draw its results after it loads.
_SETTLE_S = 5
_MAX_TEXT = 60_000
_PER_STORE = 5

_PRICE = re.compile(r"(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d+)?)", re.IGNORECASE)
_SIZE = re.compile(
    r"(?:(\d+)\s*x\s*)?(\d+(?:\.\d+)?)\s*(kg|kgs|g|gm|gms|grams?|l|ltr|litres?|liters?|ml)\b",
    re.IGNORECASE,
)
#: A product card ends at its add-to-cart button.
_CARD_END = re.compile(r"^\s*add\s*(to\s*cart)?\s*$", re.IGNORECASE)
_NOISE = re.compile(
    r"^\s*(\d+\s*mins?|\d+%\s*off|(₹|rs\.?)\s*[\d,.]+\s*off|sold out|notify me|out of stock"
    r"|[\d.]+|[\d,]+\s*ratings?)\s*$",
    re.IGNORECASE,
)
#: A product card is a handful of lines; a longer run is a page header or filters.
_MAX_CARD_LINES = 12
#: Words that make a match a by-product rather than the item itself, unless
#: the request says them.
_BY_PRODUCTS = frozenset(
    "liver gizzard gizzards neck backbone bones feet sauce masala powder seasoning soup "
    "stock pickle sausage sausages salami nuggets kebab momos biryani marinated tandoori "
    "tikka".split()
)
_STOP = frozenset({"the", "a", "an", "of", "for", "and", "fresh", "best", "cheap", "cheapest"})

_READ_PAGE = "() => document.body ? document.body.innerText : ''"


@dataclass(frozen=True)
class Offer:
    store: str
    name: str
    pack: str
    price: float
    per_unit: float | None
    unit: str


def item_words(item: str) -> list[str]:
    """The words a product name must carry: '1kg chicken' -> ['chicken']."""
    text = _SIZE.sub(" ", item.lower())
    return [w for w in re.findall(r"[a-z]+", text) if w not in _STOP and len(w) > 1]


def search_term(item: str) -> str:
    """What to type into a store's search: the item without its pack size."""
    return " ".join(item_words(item)) or item.strip()


def _pack(text: str) -> tuple[float, str] | None:
    """'2 x 450 g' -> (0.9, 'kg'); '1 pack (500 ml)' -> (0.5, 'l')."""
    m = _SIZE.search(text)
    if not m:
        return None
    count = int(m.group(1) or 1)
    amount = float(m.group(2)) * count
    unit = m.group(3).lower()
    if unit.startswith(("kg",)):
        return amount, "kg"
    if unit in {"g", "gm", "gms", "gram", "grams"}:
        return amount / 1000, "kg"
    if unit == "ml":
        return amount / 1000, "l"
    return amount, "l"


def offers_in(store: str, page_text: str, item: str) -> list[Offer]:
    """Every product card on a store's results page that is the item."""
    words = item_words(item)
    asked = set(words)
    cards: list[list[str]] = [[]]
    for raw in page_text.splitlines():
        line = raw.strip()
        if not line or _NOISE.match(line):
            continue
        if _CARD_END.match(line):
            cards.append([])
            continue
        cards[-1].append(line)
    found: list[Offer] = []
    seen: set[tuple[str, float]] = set()
    for card in cards:
        if len(card) > _MAX_CARD_LINES:
            continue
        prices = [_PRICE.search(line) for line in card]
        first = next((i for i, m in enumerate(prices) if m), None)
        if first is None:
            continue
        # The selling price is the card's first price; a second one is the MRP.
        price = float(prices[first].group(1).replace(",", ""))  # type: ignore[union-attr]
        texts = [line for line, m in zip(card, prices, strict=True) if not m]
        name = next(
            (
                t
                for t in reversed(texts)
                if all(w in t.lower() for w in words) and not _pack_only(t)
            ),
            "",
        )
        if not name or price <= 0:
            continue
        if any(w in _BY_PRODUCTS and w not in asked for w in re.findall(r"[a-z]+", name.lower())):
            continue
        pack_line = next((t for t in texts if t is not name and _pack(t)), "") or name
        pack = _pack(pack_line)
        key = (name.lower(), price)
        if key in seen:
            continue
        seen.add(key)
        per_unit = round(price / pack[0], 2) if pack and pack[0] > 0 else None
        found.append(
            Offer(
                store=store,
                name=name[:120],
                pack=pack_line[:40] if pack else "",
                price=price,
                per_unit=per_unit,
                unit=pack[1] if pack else "",
            )
        )
    return found


def _pack_only(text: str) -> bool:
    """A line that is only a pack size, like '450 g' or '1 pack (500 g)'."""
    return bool(_SIZE.search(text)) and len(re.sub(r"[\d.x()\s]|pack|kg|g|ml|l\b", "", text)) < 3


def page_text(tool_output: Any) -> str:
    """The string browser_evaluate returned, out of the MCP server's markdown."""
    text = str(tool_output or "")
    m = re.search(r"### Result\s*\n(.*?)(?:\n### |\Z)", text, re.DOTALL)
    body = (m.group(1) if m else text).strip()
    body = re.sub(r"^```\w*\s*|\s*```$", "", body)
    try:
        value = json.loads(body)
    except ValueError:
        return body
    return value if isinstance(value, str) else json.dumps(value)


def render(item: str, offers: list[Offer], problems: dict[str, str]) -> str:
    """The table the model reads: cheapest per kilo or litre first."""
    ranked = sorted(offers, key=lambda o: (o.per_unit is None, o.per_unit or 0.0, o.price))
    lines = [f"Prices for {item!r}, cheapest per unit first:"]
    for o in ranked:
        per = f" = Rs {o.per_unit:.0f}/{o.unit}" if o.per_unit is not None else ""
        pack = f", {o.pack}" if o.pack else ""
        lines.append(f"- {o.store}: {o.name}{pack}: Rs {o.price:.0f}{per}")
    if not ranked:
        lines.append("- no matching products were found on any store page")
    for store, why in problems.items():
        lines.append(f"- {store}: not checked ({why})")
    return "\n".join(lines)


@dataclass
class ComparePricesTool:
    """Opens each store's search page in the visible browser and compares prices."""

    tools: Mapping[str, Tool]
    server: str = "playwright"
    name: str = COMPARE_PRICES
    risk_tier: RiskTier = "safe"
    description: str = (
        "Compare the price of a grocery item across online stores (BigBasket, Blinkit, "
        "Zepto) in the visible browser and list the cheapest per kilo or litre. Use it "
        "for 'cheapest', 'compare prices', 'most affordable' questions about groceries."
    )
    schema: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.schema is None:
            self.schema = {
                "type": "object",
                "properties": {
                    "item": {
                        "type": "string",
                        "description": "The item with its size, e.g. '1kg chicken'.",
                    },
                    "stores": {
                        "type": "array",
                        "items": {"type": "string", "enum": sorted(STORES)},
                        "description": "Stores to check; all of them when left out.",
                    },
                },
                "required": ["item"],
            }

    def _browser(self, verb: str) -> Tool | None:
        return self.tools.get(f"{self.server}/browser_{verb}")

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        item = str(args.get("item") or "").strip()
        if not item:
            return ToolResult(success=False, output=None, error="item missing")
        navigate, evaluate = self._browser("navigate"), self._browser("evaluate")
        if navigate is None or evaluate is None:
            return ToolResult(
                success=False,
                output=None,
                error="The Playwright browser is not running; turn it on under MCP servers.",
            )
        wanted = [s for s in (args.get("stores") or []) if s in STORES] or list(STORES)
        term = quote_plus(search_term(item))
        offers: list[Offer] = []
        problems: dict[str, str] = {}
        for store in wanted:
            url = STORES[store].format(q=term)
            opened = await navigate.execute({"url": url}, ctx)
            if not opened.success:
                problems[store] = opened.error or "the page did not open"
                continue
            await asyncio.sleep(_SETTLE_S)
            read = await evaluate.execute({"function": _READ_PAGE}, ctx)
            if not read.success:
                problems[store] = read.error or "the page could not be read"
                continue
            found = offers_in(store, page_text(read.output)[:_MAX_TEXT], item)
            if not found:
                problems[store] = "no matching products on its results page"
            log.info("compare_prices: %s %d offers for %r", store, len(found), item)
            ranked = sorted(found, key=lambda o: (o.per_unit is None, o.per_unit or 0.0))
            offers.extend(ranked[:_PER_STORE])
        return ToolResult(success=bool(offers), output=render(item, offers, problems))
