"""Structured browser actions verify the real postcondition without model code."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SOURCE = Path(__file__).parents[2] / "scripts" / "browser_harness_mcp.py"
spec = importlib.util.spec_from_file_location("browser_harness_adapter", SOURCE)
assert spec and spec.loader
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def test_wait_ignores_transitional_cart_until_real_state_arrives():
    clock = Clock()
    states = iter(
        [
            {"count": 1, "text": "Updating cart"},
            {"count": 0, "text": "Your cart is getting lonely"},
        ]
    )
    result = adapter.wait_for_state(
        lambda selector: next(states),
        selector="button",
        expected_count=0,
        text="Your cart is getting lonely",
        timeout=2,
        clock=clock,
        sleep=clock.sleep,
    )
    assert result["verified"] is True
    assert result["state"]["count"] == 0
    assert clock.now > 0


def test_timeout_never_reports_success():
    clock = Clock()
    result = adapter.wait_for_state(
        lambda selector: {"count": 1, "text": "Still loading"},
        selector="button",
        expected_count=0,
        timeout=1,
        clock=clock,
        sleep=clock.sleep,
    )
    assert result["verified"] is False
    assert result["state"]["count"] == 1
    assert "timed out" in result["error"].lower()


@pytest.mark.parametrize("kwargs", [{}, {"expected_count": 0}, {"timeout": 61}, {"timeout": 0}])
def test_invalid_or_missing_postconditions_are_rejected(kwargs):
    with pytest.raises(ValueError):
        adapter.wait_for_state(lambda selector: {}, **kwargs)


def test_selector_serialization_does_not_create_executable_python():
    selector = 'button[data-testid="minus"][title="Bob\'s cart"]'
    expression = adapter.snapshot_expression(selector)
    import json

    assert json.dumps(selector) in expression
    assert "eval(" not in expression


def test_ambiguous_click_is_rejected_before_input():
    with pytest.raises(ValueError, match="ambiguous"):
        adapter.select_index(2, None)


@pytest.mark.parametrize("index", [-1, 2])
def test_out_of_range_click_is_rejected(index):
    with pytest.raises(ValueError):
        adapter.select_index(2, index)


def test_unique_click_requires_no_index():
    assert adapter.select_index(1, None) == 0


def test_missing_click_postcondition_prevents_side_effect():
    with pytest.raises(ValueError):
        adapter.validate_condition("", None, "", 10)


class Server:
    def __init__(self):
        self.tools = {}

    def tool(self, name):
        def register(function):
            self.tools[name] = function
            return function

        return register


class Browser:
    def __init__(self, matches=1):
        self.matches = matches
        self.clicks = []
        self.reads = 0

    def js(self, expression):
        if expression.endswith(").length"):
            return self.matches
        if "const selector =" in expression:
            self.reads += 1
            return {"count": 1 if self.reads == 1 else 0, "text": "Complete"}
        if "return [Math.round" in expression:
            return [20, 30]
        return None

    def wait(self, seconds):
        pass

    def click_at_xy(self, x, y):
        self.clicks.append((x, y))

    def cdp(self, method, **kwargs):
        if method == "Runtime.evaluate":
            return {"result": {"objectId": "pinned-node"}}
        if method == "Runtime.callFunctionOn":
            assert kwargs["objectId"] == "pinned-node"
            return {"result": {"value": [20, 30]}}
        return {}


def test_registered_click_requires_postcondition_before_browser_input():
    server, browser = Server(), Browser()
    adapter.install_tools(server, browser, lambda: None)
    with pytest.raises(ValueError):
        server.tools["browser_click_verified"]("#finish")
    assert not browser.clicks


def test_registered_click_rejects_ambiguity_without_side_effects():
    server, browser = Server(), Browser(matches=2)
    adapter.install_tools(server, browser, lambda: None)
    with pytest.raises(ValueError, match="ambiguous"):
        server.tools["browser_click_verified"](
            "button", expected_selector="#pending", expected_count=0
        )
    assert not browser.clicks


def test_registered_click_waits_for_actual_transition_without_reclicking():
    server, browser = Server(), Browser()
    adapter.install_tools(server, browser, lambda: None)
    result = server.tools["browser_click_verified"](
        "#finish", expected_selector="#pending", expected_count=0, expected_text="Complete"
    )
    assert result["verified"] is True
    assert result["action_performed"] is True
    assert browser.clicks == [(20, 30)]
    assert browser.reads == 2


def test_already_satisfied_state_performs_no_action_or_false_transition():
    class CompleteBrowser(Browser):
        def js(self, expression):
            return {"count": 0, "text": "Complete"}

    server, browser = Server(), CompleteBrowser()
    adapter.install_tools(server, browser, lambda: None)
    result = server.tools["browser_click_verified"](
        "#finish", expected_selector="#pending", expected_count=0
    )
    assert result["verified"] is True
    assert result["action_performed"] is False
    assert result["transition_observed"] is False
    assert not browser.clicks


def test_invalid_expected_selector_is_checked_before_action():
    class InvalidSelectorBrowser(Browser):
        def js(self, expression):
            if 'const selector = "["' in expression:
                raise ValueError("Invalid CSS selector")
            return super().js(expression)

    server, browser = Server(), InvalidSelectorBrowser()
    adapter.install_tools(server, browser, lambda: None)
    with pytest.raises(ValueError, match="Invalid CSS"):
        server.tools["browser_click_verified"]("#finish", expected_selector="[", expected_count=0)
    assert not browser.clicks


def test_changed_pinned_node_is_rejected_before_click():
    class ChangedBrowser(Browser):
        def cdp(self, method, **kwargs):
            if method == "Runtime.callFunctionOn":
                return {"exceptionDetails": {"text": "Control changed"}}
            return super().cdp(method, **kwargs)

    server, browser = Server(), ChangedBrowser()
    adapter.install_tools(server, browser, lambda: None)
    with pytest.raises(ValueError, match="changed"):
        server.tools["browser_click_verified"](
            "#finish", expected_selector="#pending", expected_count=0
        )
    assert not browser.clicks


def test_snapshot_exposes_refs_for_custom_clickable_divs():
    expression = adapter.snapshot_expression()
    assert "body *" in expression
    assert "getComputedStyle" in expression
    assert "ref:" in expression


def test_ref_identity_includes_accessible_label_and_product_context():
    expression = adapter.snapshot_expression()
    assert "label:e.getAttribute('aria-label')" in expression
    assert "context:context" in expression


def test_ref_click_does_not_require_model_authored_css_or_postcondition():
    server, browser = Server(), Browser()
    adapter.install_tools(server, browser, lambda: None)
    result = server.tools["browser_click_ref"]("observed-ref")
    assert browser.clicks == [(20, 30)]
    assert result["action_performed"] is True
    assert result["verified"] is False
    assert result["state"]["text"] == "Complete"


def test_ref_click_rejects_stale_or_used_reference_without_input():
    class StaleBrowser(Browser):
        def cdp(self, method, **kwargs):
            if method == "Runtime.evaluate":
                return {"exceptionDetails": {"text": "Stale or already used reference"}}
            return super().cdp(method, **kwargs)

    server, browser = Server(), StaleBrowser()
    adapter.install_tools(server, browser, lambda: None)
    with pytest.raises(ValueError, match="snapshot"):
        server.tools["browser_click_ref"]("old-ref")
    assert not browser.clicks


def test_ref_readback_waits_for_async_change_without_repeating_input():
    clock = Clock()
    states = iter([{"text": "Add"}, {"text": "Quantity 1"}])
    result = adapter.wait_for_change(
        lambda: next(states), {"text": "Add"}, clock=clock, sleep=clock.sleep
    )
    assert result["page_changed"] is True
    assert result["state"]["text"] == "Quantity 1"
    assert clock.now > 0


def test_unchanged_page_is_reported_without_claiming_success():
    clock = Clock()
    result = adapter.wait_for_change(
        lambda: {"text": "Add"},
        {"text": "Add"},
        timeout=1,
        clock=clock,
        sleep=clock.sleep,
    )
    assert result["page_changed"] is False


def test_ref_click_normalizes_missing_svg_text():
    class SvgBrowser(Browser):
        def cdp(self, method, **kwargs):
            declaration = kwargs.get("functionDeclaration", "")
            if "entry.text" in declaration:
                assert "(this.innerText||'')!==entry.text" in declaration
            return super().cdp(method, **kwargs)

    server, browser = Server(), SvgBrowser()
    adapter.install_tools(server, browser, lambda: None)
    server.tools["browser_click_ref"]("svg-ref")
    assert browser.clicks == [(20, 30)]
