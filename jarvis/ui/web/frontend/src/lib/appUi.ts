/**
 * The app's own controls, read and used on the assistant's behalf.
 *
 * `navigate` only opens a sidebar section. Everything inside a section — its
 * tabs, sub-pages, buttons, switches and fields — is reached through here: the
 * backend publishes an `AppUiRequest` (jarvis/core/app_ui_bridge.py), the MAIN
 * window performs it on the DOM exactly as a user's click would, and posts the
 * outcome to `POST /api/app-ui/result` under the same request id.
 *
 * Generic on purpose: a snapshot lists whatever interactive elements are
 * visible, each with a short ref (`e1`, `e2` …) stored as `data-jarvis-ref`,
 * so no view needs a registry entry to become usable by voice.
 *
 * Two refusals are built in. A control the app marks destructive (the red
 * `destructive` button variant, `data-danger`, `data-destructive`) is never
 * clicked: the person confirms those themselves. A password field is never
 * read back or typed into.
 */
import { SECTION_LABELS, useEventStore } from "@/store/events";
import { isAutopilotToastsEnabled } from "@/lib/autopilotToasts";
import { translate } from "@/i18n";

export type AppUiAction = "snapshot" | "click" | "type" | "scroll";

export interface AppUiRequestPayload {
  request_id?: string;
  action?: string;
  ref?: string;
  label?: string;
  text?: string;
  submit?: boolean;
  direction?: string;
  wait_ms?: number;
}

export type UiRegion = "dialog" | "main" | "nav" | "other";

export interface UiControl {
  ref: string;
  role: string;
  name: string;
  region: UiRegion;
  value?: string;
  checked?: boolean | "mixed";
  selected?: boolean;
  expanded?: boolean;
  disabled?: boolean;
  destructive?: boolean;
}

export interface UiSnapshot {
  section: string;
  section_label: string;
  headings: string[];
  scroll: string;
  /** Every visible control, of which at most `MAX_CONTROLS` are listed. */
  total: number;
  elements: UiControl[];
}

export interface AppUiOutcome {
  ok: boolean;
  result?: Record<string, unknown>;
  error?: string;
}

export interface ScreenInfo {
  section: string;
  section_label: string;
}

export const MAX_CONTROLS = 120;
const MAX_NAME = 60;
const MAX_VALUE = 60;
const POLL_MS = 150;
const SETTLE_MS = 150;
/** Upper bound for a caller's wait, so one request can never hang the window. */
const MAX_WAIT_MS = 5000;

const INTERACTIVE = [
  "button",
  "a[href]",
  "input:not([type=hidden])",
  "textarea",
  "select",
  '[contenteditable="true"]',
  '[contenteditable=""]',
  '[contenteditable="plaintext-only"]',
  '[role="tab"]',
  '[role="button"]',
  '[role="link"]',
  '[role="menuitem"]',
  '[role="menuitemcheckbox"]',
  '[role="menuitemradio"]',
  '[role="switch"]',
  '[role="checkbox"]',
  '[role="radio"]',
  '[role="option"]',
  '[role="combobox"]',
  '[role="textbox"]',
].join(", ");

const DIALOG = '[role="dialog"], [role="alertdialog"], dialog[open]';
const NAV = 'aside, nav, [role="navigation"]';
const DESTRUCTIVE = '[data-danger], [data-destructive], [class*="bg-destructive"], [class*="text-destructive"]';
const WORK_TRACE = '[data-testid="work-trace"], [data-trace-tool], [data-trace-summary]';
const REGION_RANK: Record<UiRegion, number> = { dialog: 0, main: 1, nav: 2, other: 3 };

// --------------------------------------------------------------------------
// Reading the DOM
// --------------------------------------------------------------------------

function clip(text: string, limit: number): string {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > limit ? `${flat.slice(0, limit - 1)}…` : flat;
}

/** Lower-case, punctuation-free form used to compare a spoken label with a name. */
export function normalizeLabel(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .trim();
}

function visibleText(el: Element): string {
  // innerText leaves out hidden text in a real engine; jsdom has none, so
  // textContent stands in there.
  const inner = (el as HTMLElement).innerText;
  return typeof inner === "string" ? inner : (el.textContent ?? "");
}

function isEditable(el: Element): boolean {
  const flag = el.getAttribute("contenteditable");
  return flag === "" || flag === "true" || flag === "plaintext-only";
}

function isTextInput(el: Element): el is HTMLInputElement | HTMLTextAreaElement {
  if (el instanceof HTMLTextAreaElement) return true;
  if (!(el instanceof HTMLInputElement)) return false;
  return !["checkbox", "radio", "button", "submit", "reset", "image", "file", "range", "color", "hidden"].includes(
    el.type,
  );
}

function isField(el: Element): boolean {
  return isTextInput(el) || el instanceof HTMLSelectElement || isEditable(el) || el.getAttribute("role") === "textbox";
}

function roleOf(el: Element): string {
  const explicit = el.getAttribute("role");
  if (explicit) return explicit;
  if (el instanceof HTMLButtonElement) return "button";
  if (el instanceof HTMLAnchorElement) return "link";
  if (el instanceof HTMLSelectElement) return "select";
  if (el instanceof HTMLTextAreaElement) return "textbox";
  if (el instanceof HTMLInputElement) {
    switch (el.type) {
      case "checkbox":
        return "checkbox";
      case "radio":
        return "radio";
      case "button":
      case "submit":
      case "reset":
      case "image":
        return "button";
      case "range":
        return "slider";
      case "search":
        return "searchbox";
      case "password":
        return "password";
      default:
        return "textbox";
    }
  }
  if (isEditable(el)) return "textbox";
  return el.tagName.toLowerCase();
}

/** The words a person would use for the control, in accessible-name order. */
export function accessibleName(el: Element): string {
  const aria = el.getAttribute("aria-label");
  if (aria?.trim()) return clip(aria, MAX_NAME);
  const labelledBy = el.getAttribute("aria-labelledby");
  if (labelledBy) {
    const text = labelledBy
      .split(/\s+/)
      .map((id) => el.ownerDocument.getElementById(id)?.textContent ?? "")
      .join(" ");
    if (text.trim()) return clip(text, MAX_NAME);
  }
  if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement) {
    const label = el.labels?.[0];
    if (label?.textContent?.trim()) return clip(label.textContent, MAX_NAME);
  }
  if (!isField(el)) {
    const text = visibleText(el);
    if (text.trim()) return clip(text, MAX_NAME);
  }
  const placeholder = el.getAttribute("placeholder");
  if (placeholder?.trim()) return clip(placeholder, MAX_NAME);
  const title = el.getAttribute("title");
  if (title?.trim()) return clip(title, MAX_NAME);
  if (el instanceof HTMLInputElement && ["button", "submit", "reset"].includes(el.type) && el.value.trim()) {
    return clip(el.value, MAX_NAME);
  }
  const testId = el.getAttribute("data-testid");
  return testId ? clip(testId.replace(/[-_]+/g, " "), MAX_NAME) : "";
}

export function isVisible(el: Element): boolean {
  if (el.closest('[hidden], [aria-hidden="true"], [inert]')) return false;
  if (el.getClientRects().length === 0) return false;
  const view = el.ownerDocument.defaultView;
  const style = view?.getComputedStyle(el);
  return !(style && (style.visibility === "hidden" || style.display === "none"));
}

function isDisabled(el: Element): boolean {
  if ((el as HTMLButtonElement).disabled === true) return true;
  return el.closest('[aria-disabled="true"], fieldset[disabled]') !== null;
}

export function isDestructive(el: Element): boolean {
  return el.matches(DESTRUCTIVE) || el.closest("[data-danger], [data-destructive]") !== null;
}

function regionOf(el: Element): UiRegion {
  if (el.closest(DIALOG)) return "dialog";
  if (el.closest(NAV)) return "nav";
  if (el.closest("main")) return "main";
  return "other";
}

function openDialog(doc: Document): HTMLElement | null {
  const dialogs = Array.from(doc.querySelectorAll<HTMLElement>(DIALOG)).filter(isVisible);
  return dialogs.length ? dialogs[dialogs.length - 1] : null;
}

function triState(value: string | null): boolean | "mixed" | undefined {
  if (value === "true") return true;
  if (value === "false") return false;
  if (value === "mixed") return "mixed";
  return undefined;
}

interface Found {
  el: HTMLElement;
  control: UiControl;
  order: number;
}

function describe(el: HTMLElement, order: number): Found {
  const role = roleOf(el);
  const control: UiControl = { ref: "", role, name: accessibleName(el), region: regionOf(el) };
  if (el instanceof HTMLInputElement && el.type === "password") {
    control.value = el.value ? "***" : "";
  } else if (isTextInput(el)) {
    control.value = clip(el.value, MAX_VALUE);
  } else if (el instanceof HTMLSelectElement) {
    control.value = clip(el.selectedOptions[0]?.textContent ?? el.value, MAX_VALUE);
  } else if (isEditable(el)) {
    control.value = clip(el.textContent ?? "", MAX_VALUE);
  }
  if (el instanceof HTMLInputElement && (el.type === "checkbox" || el.type === "radio")) {
    control.checked = el.indeterminate ? "mixed" : el.checked;
  } else {
    const checked = triState(el.getAttribute("aria-checked")) ?? triState(el.getAttribute("aria-pressed"));
    if (checked !== undefined) control.checked = checked;
  }
  if (el.getAttribute("aria-selected") === "true") control.selected = true;
  const expanded = triState(el.getAttribute("aria-expanded"));
  if (typeof expanded === "boolean") control.expanded = expanded;
  if (isDisabled(el)) control.disabled = true;
  if (isDestructive(el)) control.destructive = true;
  return { el, control, order };
}

/** Every visible control, dialog first, then main content, then navigation. */
function collect(doc: Document): Found[] {
  const found: Found[] = [];
  const seen = new Set<Element>();
  doc.querySelectorAll<HTMLElement>(INTERACTIVE).forEach((el, order) => {
    if (seen.has(el) || !isVisible(el)) return;
    seen.add(el);
    found.push(describe(el, order));
  });
  return found.sort(
    (a, b) => REGION_RANK[a.control.region] - REGION_RANK[b.control.region] || a.order - b.order,
  );
}

function headings(doc: Document): string[] {
  const scope: ParentNode = openDialog(doc) ?? doc.querySelector("main") ?? doc.body;
  const out: string[] = [];
  scope
    .querySelectorAll<HTMLElement>('h1, h2, [role="heading"][aria-level="1"], [role="heading"][aria-level="2"]')
    .forEach((el) => {
      if (out.length >= 4 || !isVisible(el)) return;
      const text = clip(visibleText(el), MAX_NAME);
      if (text && !out.includes(text)) out.push(text);
    });
  return out;
}

function scrollContainer(doc: Document): HTMLElement | null {
  const scope = openDialog(doc) ?? doc.querySelector<HTMLElement>("main") ?? doc.body;
  if (!scope) return null;
  const view = doc.defaultView;
  let best: HTMLElement | null = null;
  let bestArea = 0;
  for (const node of [scope, ...Array.from(scope.querySelectorAll<HTMLElement>("*"))]) {
    if (node.scrollHeight <= node.clientHeight + 4) continue;
    const overflow = view?.getComputedStyle(node).overflowY ?? "";
    if (overflow !== "auto" && overflow !== "scroll" && overflow !== "overlay") continue;
    const area = node.clientWidth * node.clientHeight;
    if (area > bestArea) {
      best = node;
      bestArea = area;
    }
  }
  return best ?? (doc.scrollingElement as HTMLElement | null);
}

function scrollPosition(node: HTMLElement | null): string {
  if (!node || node.scrollHeight <= node.clientHeight + 4) return "everything fits, nothing to scroll";
  const max = node.scrollHeight - node.clientHeight;
  if (node.scrollTop <= 4) return "top, more below";
  if (node.scrollTop >= max - 4) return "bottom, more above";
  return `${Math.round((node.scrollTop / max) * 100)}% down, more above and below`;
}

function clearRefs(doc: Document): void {
  doc.querySelectorAll("[data-jarvis-ref]").forEach((el) => el.removeAttribute("data-jarvis-ref"));
}

export function takeSnapshot(doc: Document, screen: ScreenInfo): UiSnapshot {
  clearRefs(doc);
  const found = collect(doc);
  const listed = found.slice(0, MAX_CONTROLS);
  listed.forEach((item, index) => {
    const ref = `e${index + 1}`;
    item.el.setAttribute("data-jarvis-ref", ref);
    item.control.ref = ref;
  });
  return {
    ...screen,
    headings: headings(doc),
    scroll: scrollPosition(scrollContainer(doc)),
    total: found.length,
    elements: listed.map((item) => item.control),
  };
}

// --------------------------------------------------------------------------
// Finding one control
// --------------------------------------------------------------------------

function roleRank(role: string): number {
  if (role === "tab") return 0;
  if (["button", "link", "menuitem", "option", "radio", "switch", "checkbox"].includes(role)) return 1;
  return 2;
}

function bigrams(text: string): Set<string> {
  const out = new Set<string>();
  for (let i = 0; i < text.length - 1; i += 1) out.add(text.slice(i, i + 2));
  return out;
}

function similarity(a: string, b: string): number {
  const x = bigrams(a);
  const y = bigrams(b);
  if (!x.size || !y.size) return 0;
  let shared = 0;
  x.forEach((gram) => {
    if (y.has(gram)) shared += 1;
  });
  return (2 * shared) / (x.size + y.size);
}

type Pick = { found: Found } | { error: string };

/** Best control named like `label`: exact name, then prefix, then substring. */
export function findByLabel(doc: Document, label: string, fieldsOnly = false): Pick {
  const query = normalizeLabel(label);
  if (!query) return { error: "The label is empty." };
  // A turn's work trace lists earlier steps ("App ui click Windows Update
  // 1.6s"); a spoken label never means one of those. Live 2026-10-26 a click
  // for "Windows Update" landed on that trace row and reported success.
  const candidates = collect(doc).filter(
    (item) => !item.control.disabled && (!fieldsOnly || isField(item.el)) && !item.el.closest(WORK_TRACE),
  );
  let best: { item: Found; rank: number[] } | null = null;
  for (const item of candidates) {
    const name = normalizeLabel(item.control.name);
    if (!name) continue;
    const tier = name === query ? 0 : name.startsWith(query) ? 1 : name.includes(query) ? 2 : -1;
    if (tier < 0) continue;
    const region = item.control.region === "dialog" || item.control.region === "main" ? 0 : 1;
    const rank = [tier, region, roleRank(item.control.role), item.order];
    if (!best || compareRanks(rank, best.rank) < 0) best = { item, rank };
  }
  if (best) return { found: best.item };
  const closest = Array.from(
    new Set(
      candidates
        .map((item) => ({ name: item.control.name, score: similarity(normalizeLabel(item.control.name), query) }))
        .filter((entry) => entry.name && entry.score > 0)
        .sort((a, b) => b.score - a.score)
        .map((entry) => entry.name),
    ),
  ).slice(0, 5);
  const kind = fieldsOnly ? "field" : "control";
  return {
    error: closest.length
      ? `No ${kind} named "${label}" on this screen; closest: ${closest.map((n) => `"${n}"`).join(", ")}.`
      : `No ${kind} named "${label}" on this screen.`,
  };
}

function compareRanks(a: number[], b: number[]): number {
  for (let i = 0; i < a.length; i += 1) {
    if (a[i] !== b[i]) return a[i] - b[i];
  }
  return 0;
}

function byRef(doc: Document, ref: string): Pick {
  if (!/^e\d+$/.test(ref)) return { error: `"${ref}" is not a ref; refs look like e5.` };
  const el = doc.querySelector<HTMLElement>(`[data-jarvis-ref="${ref}"]`);
  if (!el || !el.isConnected || !isVisible(el)) {
    return { error: `Ref ${ref} is not on the screen any more; take a new app_ui_snapshot.` };
  }
  return { found: describe(el, 0) };
}

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

async function locate(doc: Document, req: AppUiRequestPayload, fieldsOnly: boolean): Promise<Pick> {
  const ref = (req.ref ?? "").trim();
  if (ref) return byRef(doc, ref);
  const label = (req.label ?? "").trim();
  if (!label) return { error: "Pass a ref from app_ui_snapshot or a label." };
  // Right after a section switch the view may still be rendering (a lazily
  // loaded section, a tab bar that mounts a frame later): wait for it.
  const deadline = Date.now() + Math.min(Math.max(0, req.wait_ms ?? 0), MAX_WAIT_MS);
  let pick = findByLabel(doc, label, fieldsOnly);
  while ("error" in pick && Date.now() < deadline) {
    await sleep(POLL_MS);
    pick = findByLabel(doc, label, fieldsOnly);
  }
  return pick;
}

// --------------------------------------------------------------------------
// Acting
// --------------------------------------------------------------------------

function summary(doc: Document, screen: () => ScreenInfo): Record<string, unknown> {
  return { ...screen(), headings: headings(doc), count: collect(doc).length };
}

function setNativeValue(el: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement, value: string): void {
  // React keeps the last value on the node and drops an input event whose value
  // looks unchanged; the prototype's own setter goes around that.
  const proto =
    el instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : el instanceof HTMLSelectElement
        ? HTMLSelectElement.prototype
        : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
  if (setter) setter.call(el, value);
  else el.value = value;
}

function pressEnter(el: HTMLElement): void {
  const init: KeyboardEventInit = { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true, cancelable: true };
  const proceed = el.dispatchEvent(new KeyboardEvent("keydown", init));
  el.dispatchEvent(new KeyboardEvent("keypress", init));
  el.dispatchEvent(new KeyboardEvent("keyup", init));
  const form = (el as HTMLInputElement).form;
  if (proceed && form && typeof form.requestSubmit === "function") form.requestSubmit();
}

async function click(doc: Document, req: AppUiRequestPayload, screen: () => ScreenInfo): Promise<AppUiOutcome> {
  const pick = await locate(doc, req, false);
  if ("error" in pick) return { ok: false, error: pick.error };
  const { el, control } = pick.found;
  if (control.destructive) {
    return {
      ok: false,
      error: `"${control.name}" is a destructive control (delete, remove, reset). The user has to click it themselves.`,
    };
  }
  if (control.disabled) return { ok: false, error: `"${control.name}" is disabled right now.` };
  if (typeof el.scrollIntoView === "function") el.scrollIntoView({ block: "center", inline: "nearest" });
  el.click();
  await sleep(SETTLE_MS);
  return {
    ok: true,
    result: { clicked: { name: control.name, role: control.role, region: control.region }, ...summary(doc, screen) },
  };
}

function selectOption(el: HTMLSelectElement, text: string): boolean {
  const query = normalizeLabel(text);
  const options = Array.from(el.options);
  const option =
    options.find((o) => normalizeLabel(o.textContent ?? "") === query || normalizeLabel(o.value) === query) ??
    options.find((o) => normalizeLabel(o.textContent ?? "").startsWith(query)) ??
    options.find((o) => normalizeLabel(o.textContent ?? "").includes(query));
  if (!option) return false;
  setNativeValue(el, option.value);
  return true;
}

async function type(doc: Document, req: AppUiRequestPayload, screen: () => ScreenInfo): Promise<AppUiOutcome> {
  const pick = await locate(doc, req, true);
  if ("error" in pick) return { ok: false, error: pick.error };
  const { el, control } = pick.found;
  if (el instanceof HTMLInputElement && el.type === "password") {
    return { ok: false, error: "That is a password field. The user types passwords themselves." };
  }
  if (!isField(el)) {
    return { ok: false, error: `"${control.name}" is a ${control.role}, not a text field; click it instead.` };
  }
  if (control.disabled || (el as HTMLInputElement).readOnly) {
    return { ok: false, error: `"${control.name}" cannot be edited right now.` };
  }
  const text = req.text ?? "";
  if (typeof el.scrollIntoView === "function") el.scrollIntoView({ block: "center", inline: "nearest" });
  el.focus();
  if (el instanceof HTMLSelectElement) {
    if (!selectOption(el, text)) return { ok: false, error: `"${control.name}" has no option "${text}".` };
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  } else if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) {
    setNativeValue(el, text);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  } else {
    el.textContent = text;
    el.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: text }));
  }
  if (req.submit) pressEnter(el);
  await sleep(SETTLE_MS);
  return {
    ok: true,
    result: {
      typed_into: { name: control.name, role: control.role },
      submitted: req.submit === true,
      ...summary(doc, screen),
    },
  };
}

async function scroll(doc: Document, req: AppUiRequestPayload): Promise<AppUiOutcome> {
  const direction = (req.direction ?? "").toLowerCase();
  if (!["up", "down", "top", "bottom"].includes(direction)) {
    return { ok: false, error: "direction must be up, down, top or bottom." };
  }
  const node = scrollContainer(doc);
  if (!node) return { ok: true, result: { scrolled: false, position: scrollPosition(null) } };
  const before = node.scrollTop;
  const page = Math.max(node.clientHeight * 0.8, 100);
  const target =
    direction === "top"
      ? 0
      : direction === "bottom"
        ? node.scrollHeight
        : before + (direction === "down" ? page : -page);
  if (typeof node.scrollTo === "function") node.scrollTo({ top: target });
  else node.scrollTop = target;
  await sleep(SETTLE_MS);
  return { ok: true, result: { scrolled: node.scrollTop !== before, position: scrollPosition(node) } };
}

export function currentScreen(): ScreenInfo {
  const section = useEventStore.getState().activeSection;
  return { section, section_label: SECTION_LABELS[section] ?? section };
}

/** Perform one request on `doc`. Never throws: a failure is an outcome. */
export async function performAppUi(
  req: AppUiRequestPayload,
  doc: Document = document,
  screen: () => ScreenInfo = currentScreen,
): Promise<AppUiOutcome> {
  try {
    switch (req.action) {
      case "snapshot":
        await sleep(0);
        return { ok: true, result: { ...takeSnapshot(doc, screen()) } };
      case "click":
        return await click(doc, req, screen);
      case "type":
        return await type(doc, req, screen);
      case "scroll":
        return await scroll(doc, req);
      default:
        return { ok: false, error: `Unknown app UI action "${String(req.action)}".` };
    }
  } catch (err) {
    console.warn("app UI action failed", req.action, err);
    return { ok: false, error: `The app could not do that: ${err instanceof Error ? err.message : String(err)}` };
  }
}

// --------------------------------------------------------------------------
// The WebSocket entry point
// --------------------------------------------------------------------------

export type ResultPoster = (requestId: string, outcome: AppUiOutcome) => Promise<void>;

export const postAppUiResult: ResultPoster = async (requestId, outcome) => {
  try {
    const response = await fetch("/api/app-ui/result", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        request_id: requestId,
        ok: outcome.ok,
        result: outcome.result ?? null,
        error: outcome.error ?? null,
      }),
    });
    if (!response.ok) console.warn("app UI result was refused", response.status);
  } catch (err) {
    // The tool times out with "the window did not answer"; say why here.
    console.warn("app UI result could not be posted", err);
  }
};

function toastFor(req: AppUiRequestPayload, outcome: AppUiOutcome): void {
  if (!outcome.ok || !isAutopilotToastsEnabled()) return;
  const result = outcome.result ?? {};
  const clicked = result.clicked as { name?: string } | undefined;
  const typed = result.typed_into as { name?: string } | undefined;
  const store = useEventStore.getState();
  if (req.action === "click" && clicked?.name) {
    store.pushToast("info", `${translate("use_web_socket.jarvis_clicked")} ${clicked.name}`);
  } else if (req.action === "type" && typed?.name) {
    store.pushToast("info", `${translate("use_web_socket.jarvis_typed")} ${typed.name}`);
  }
}

let queue: Promise<void> = Promise.resolve();

/**
 * Handle an `AppUiRequest` envelope. Only the main window answers: a detached
 * solo window shows one pinned section and the request means the main one.
 * Requests run one at a time, so a click never lands mid-snapshot.
 */
export function handleAppUiRequest(payload: AppUiRequestPayload, post: ResultPoster = postAppUiResult): Promise<void> {
  if (useEventStore.getState().solo) return Promise.resolve();
  const requestId = (payload.request_id ?? "").trim();
  if (!requestId) return Promise.resolve();
  const run = queue.then(async () => {
    const outcome = await performAppUi(payload);
    toastFor(payload, outcome);
    await post(requestId, outcome);
  });
  queue = run.catch((err) => console.warn("app UI request failed", err));
  return run;
}
