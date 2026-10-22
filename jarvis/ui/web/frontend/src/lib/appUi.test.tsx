import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
import { useState } from "react";

import {
  handleAppUiRequest,
  performAppUi,
  takeSnapshot,
  type AppUiOutcome,
  type UiSnapshot,
} from "./appUi";
import { useEventStore } from "@/store/events";

/*
 * The assistant reaches everything inside a section through these four
 * actions, on whatever the view renders. jsdom lays nothing out, so every
 * element would report zero client rects; the stub below gives each element a
 * box unless it, or an ancestor, is display:none — the same answer a real
 * engine gives.
 */

const SCREEN = () => ({ section: "settings", section_label: "Settings" });

function fixture(): void {
  document.body.innerHTML = `
    <aside><nav>
      <button id="nav-chats">Chats</button>
      <button id="nav-settings">Settings</button>
    </nav></aside>
    <main>
      <h1>Settings</h1>
      <div role="tablist">
        <button role="tab" aria-selected="true" id="tab-general">General</button>
        <button role="tab" aria-selected="false" id="tab-model-routing">Model routing</button>
        <button role="tab" aria-selected="false" id="tab-model">Model</button>
      </div>
      <button id="main-settings">Settings</button>
      <label>API name <input id="api-name" value="abc"></label>
      <input type="password" aria-label="Secret" value="hunter2">
      <button hidden>Ghost</button>
      <div style="display:none"><button>Hidden child</button></div>
      <button aria-hidden="true">Aria hidden</button>
      <button class="bg-destructive text-destructive-foreground" id="wipe">Delete everything</button>
      <button role="switch" aria-checked="true">Autopilot toasts</button>
    </main>`;
}

function clicks(): string[] {
  const seen: string[] = [];
  document.querySelectorAll("button").forEach((button) =>
    button.addEventListener("click", () => seen.push(button.id || button.textContent || "")),
  );
  return seen;
}

function snapshot(): UiSnapshot {
  return takeSnapshot(document, SCREEN());
}

beforeEach(() => {
  vi.spyOn(Element.prototype, "getClientRects").mockImplementation(function (this: Element) {
    for (let node: Element | null = this; node; node = node.parentElement) {
      if (getComputedStyle(node).display === "none") return [] as unknown as DOMRectList;
    }
    return [{ x: 0, y: 0, width: 10, height: 10 }] as unknown as DOMRectList;
  });
  useEventStore.setState({ solo: false });
});

afterEach(() => {
  vi.restoreAllMocks();
  document.body.replaceChildren();
});

describe("snapshot", () => {
  it("lists the visible controls with refs, main content before navigation", () => {
    fixture();
    const snap = snapshot();
    expect(snap.elements.map((e) => e.name)).toEqual([
      "General",
      "Model routing",
      "Model",
      "Settings",
      "API name",
      "Secret",
      "Delete everything",
      "Autopilot toasts",
      "Chats",
      "Settings",
    ]);
    expect(snap.elements.map((e) => e.ref)).toEqual(snap.elements.map((_, i) => `e${i + 1}`));
    expect(document.querySelector("#tab-model")?.getAttribute("data-jarvis-ref")).toBe("e3");
    expect(snap.headings).toEqual(["Settings"]);
    expect(snap.section).toBe("settings");
    expect(snap.total).toBe(10);
  });

  it("reports roles, regions and state", () => {
    fixture();
    const byName = Object.fromEntries(snapshot().elements.map((e) => [`${e.region}:${e.name}`, e]));
    expect(byName["main:General"]).toMatchObject({ role: "tab", selected: true });
    expect(byName["main:API name"]).toMatchObject({ role: "textbox", value: "abc" });
    expect(byName["main:Autopilot toasts"]).toMatchObject({ role: "switch", checked: true });
    expect(byName["main:Delete everything"]).toMatchObject({ destructive: true });
    expect(byName["nav:Chats"]).toMatchObject({ role: "button", region: "nav" });
  });

  it("leaves out hidden controls and never returns a password", () => {
    fixture();
    const snap = snapshot();
    const names = snap.elements.map((e) => e.name);
    expect(names).not.toContain("Ghost");
    expect(names).not.toContain("Hidden child");
    expect(names).not.toContain("Aria hidden");
    expect(JSON.stringify(snap)).not.toContain("hunter2");
    expect(snap.elements.find((e) => e.name === "Secret")?.value).toBe("***");
  });

  it("lists an open dialog first and clears old refs", () => {
    fixture();
    snapshot();
    const dialog = document.createElement("div");
    dialog.setAttribute("role", "dialog");
    dialog.innerHTML = `<button>Confirm</button>`;
    document.body.append(dialog);
    const snap = snapshot();
    expect(snap.elements[0]).toMatchObject({ ref: "e1", name: "Confirm", region: "dialog" });
    expect(document.querySelectorAll('[data-jarvis-ref="e1"]')).toHaveLength(1);
  });
});

describe("click", () => {
  it("prefers the exact name over a longer one that starts the same", async () => {
    fixture();
    const seen = clicks();
    const out = await performAppUi({ action: "click", label: "model" }, document, SCREEN);
    expect(out.ok).toBe(true);
    expect(seen).toEqual(["tab-model"]);
    expect(out.result?.clicked).toMatchObject({ name: "Model", role: "tab" });
  });

  it("prefers the main content over the sidebar for the same name", async () => {
    fixture();
    const seen = clicks();
    await performAppUi({ action: "click", label: "Settings" }, document, SCREEN);
    expect(seen).toEqual(["main-settings"]);
  });

  it("clicks by ref from the last snapshot", async () => {
    fixture();
    const seen = clicks();
    snapshot();
    const out = await performAppUi({ action: "click", ref: "e2" }, document, SCREEN);
    expect(out.ok).toBe(true);
    expect(seen).toEqual(["tab-model-routing"]);
  });

  it("refuses a destructive control", async () => {
    fixture();
    const seen = clicks();
    const out = await performAppUi({ action: "click", label: "Delete everything" }, document, SCREEN);
    expect(out.ok).toBe(false);
    expect(out.error).toContain("user has to click it themselves");
    expect(seen).toEqual([]);
  });

  it("names the closest controls when nothing matches", async () => {
    fixture();
    const out = await performAppUi({ action: "click", label: "Modle" }, document, SCREEN);
    expect(out.ok).toBe(false);
    expect(out.error).toMatch(/No control named "Modle".*closest: .*"Model"/);
  });

  it("waits for a control that renders after the request arrived", async () => {
    fixture();
    setTimeout(() => {
      const late = document.createElement("button");
      late.id = "late";
      late.textContent = "Graph";
      document.querySelector("main")?.append(late);
    }, 200);
    const out = await performAppUi({ action: "click", label: "graph", wait_ms: 1500 }, document, SCREEN);
    expect(out.ok).toBe(true);
    expect(out.result?.clicked).toMatchObject({ name: "Graph" });
  });
});

describe("type", () => {
  function Search({ onValue, onEnter }: { onValue: (v: string) => void; onEnter: () => void }) {
    const [value, setValue] = useState("");
    return (
      <main>
        <label>
          Search
          <input
            value={value}
            onChange={(event) => {
              setValue(event.target.value);
              onValue(event.target.value);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter") onEnter();
            }}
          />
        </label>
        <input type="password" aria-label="Password" defaultValue="" />
      </main>
    );
  }

  it("fires React's onChange and presses Enter on submit", async () => {
    const values: string[] = [];
    let entered = 0;
    render(<Search onValue={(v) => values.push(v)} onEnter={() => (entered += 1)} />);
    const out = await performAppUi(
      { action: "type", label: "search", text: "weather", submit: true },
      document,
      SCREEN,
    );
    expect(out.ok).toBe(true);
    expect(values).toEqual(["weather"]);
    expect(entered).toBe(1);
    expect((document.querySelector("input") as HTMLInputElement).value).toBe("weather");
    expect(out.result).toMatchObject({ typed_into: { name: "Search" }, submitted: true });
  });

  it("refuses a password field", async () => {
    render(<Search onValue={() => undefined} onEnter={() => undefined} />);
    const out = await performAppUi({ action: "type", label: "Password", text: "x" }, document, SCREEN);
    expect(out.ok).toBe(false);
    expect(out.error).toContain("password");
  });
});

describe("handleAppUiRequest", () => {
  it("answers from the main window under the request id", async () => {
    fixture();
    const posted: Array<[string, AppUiOutcome]> = [];
    await handleAppUiRequest({ request_id: "r1", action: "snapshot" }, async (id, outcome) => {
      posted.push([id, outcome]);
    });
    expect(posted).toHaveLength(1);
    expect(posted[0][0]).toBe("r1");
    expect(posted[0][1].ok).toBe(true);
  });

  it("stays silent in a detached solo window", async () => {
    fixture();
    useEventStore.setState({ solo: true });
    const posted: string[] = [];
    await handleAppUiRequest({ request_id: "r2", action: "snapshot" }, async (id) => {
      posted.push(id);
    });
    expect(posted).toEqual([]);
  });
});
