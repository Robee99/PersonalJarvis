import { useCallback, useEffect, useState } from "react";
import { Bot, Check, Cloud, Cpu, Loader2, Plus, RefreshCw, Sparkles } from "lucide-react";
import {
  getBrainProviderModels,
  saveBrainProviderModel,
  switchBrainProvider,
  type BrainModel,
} from "@/hooks/useProviders";
import { isHermesBrain } from "@/hooks/useHermesBrain";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";

/**
 * The Model page's one place to choose (2026-10-11).
 *
 * Before, the page was the Hermes card followed by every brain provider in
 * the catalogue, each with its own "Set active", and a local llama.cpp model
 * hid behind a card called "Local server (OpenAI-compatible)" that needed the
 * port typed in, plus a matching provider in Hermes's config.yaml. Now the
 * choice is one list:
 *
 * - **Hermes decides** — Hermes picks per turn (the default);
 * - **On this computer** — every local server Jarvis finds on the usual ports
 *   (llama.cpp, LM Studio, vLLM, Ollama) and every one Hermes already names,
 *   with whether it is running. One click makes it Hermes's model; an address
 *   typed under "Add a server" does the same for any other port;
 * - **Free cloud** — the free models Hermes reaches through Nous.
 *
 * Without Hermes as the brain the list shows the local servers only, and
 * "Use" makes Jarvis's own local-server card the brain.
 *
 * Above the list, one switch says who answers right now, Hermes Agent or
 * Jarvis directly, and switches it (2026-10-12: a switch to Hermes made in
 * the provider grid below left this list showing the old brain, so it read
 * as if the switch had not happened). The live brain comes from the event
 * store (`BrainProviderSwitched`), and every switch rescans the list.
 *
 * Backend: `GET/POST /api/local-servers` (`local_server_routes.py`), the
 * Hermes card's model (`PUT /api/providers/hermes/model`) and
 * `POST /api/brain/switch`.
 */

export interface LocalServer {
  url: string;
  port: number;
  kind: string;
  state: "running" | "loading" | "off";
  models: string[];
  hermes_provider: string;
  hermes_model: string;
}

interface LocalServersResult {
  hermes_brain: boolean;
  selection: string;
  servers: LocalServer[];
}

export async function listLocalServers(): Promise<LocalServersResult> {
  const res = await fetch("/api/local-servers");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as LocalServersResult;
}

async function connectLocalServer(url: string, model: string): Promise<{ selection: string; brain: string }> {
  const res = await fetch("/api/local-servers/use", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url, model }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail ?? `HTTP ${res.status}`);
  return body;
}

/** The ids "Hermes decides" is stored as. */
const HERMES_AUTO = new Set(["", "hermes-agent"]);

interface Choice {
  key: string;
  /** What is selected when this row is on. */
  selection: string;
  title: string;
  meta: string;
  state?: LocalServer["state"];
  pick: () => Promise<unknown>;
}

function hostOf(url: string): string {
  return url.replace(/^https?:\/\//, "");
}

/** `local-qwen::qwen` for a Hermes provider, `url|model` for Jarvis's own card. */
function localSelection(hermes: boolean, server: LocalServer, model: string): string {
  if (hermes) {
    return server.hermes_provider ? `${server.hermes_provider}::${model}` : `new:${server.url}|${model}`;
  }
  return `${server.url}|${model}`;
}

/** The model part of a Hermes selection, for the status line. */
function modelName(selection: string): string {
  const model = selection.includes("::") ? selection.split("::").slice(1).join("::") : selection;
  return model.replace(/^https?:\/\/[^|]*\|/, "");
}

export function BrainPicker({
  onChanged,
  onPickOther,
}: {
  onChanged?: () => void;
  /** "Jarvis directly" was picked: show the full provider list to choose from. */
  onPickOther?: () => void;
}) {
  const t = useT();
  const [data, setData] = useState<LocalServersResult | null>(null);
  const [cloud, setCloud] = useState<BrainModel[]>([]);
  const [scanning, setScanning] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [address, setAddress] = useState("");
  const [adding, setAdding] = useState(false);

  const scan = useCallback(async () => {
    setScanning(true);
    try {
      setData(await listLocalServers());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setScanning(false);
    }
  }, []);

  useEffect(() => {
    void scan();
    const onSwitched = () => void scan();
    window.addEventListener("jarvis:brain-switched", onSwitched);
    return () => window.removeEventListener("jarvis:brain-switched", onSwitched);
  }, [scan]);

  // The live brain wins over the last scan, which a switch elsewhere outdates.
  const liveBrain = useEventStore((s) => s.brainProvider);
  const known = Boolean(liveBrain) && liveBrain !== "unknown";
  const hermes = known ? isHermesBrain(liveBrain) : data?.hermes_brain ?? false;
  const [switched, setSwitched] = useState(false);
  useEffect(() => {
    if (!hermes) return;
    getBrainProviderModels("hermes")
      .then((r) => setCloud(r.models.filter((m) => m.id.startsWith("nous::"))))
      .catch(() => setCloud([]));
  }, [hermes]);

  const run = async (key: string, action: () => Promise<unknown>) => {
    setBusy(key);
    setError(null);
    try {
      await action();
      await scan();
      onChanged?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const chooseServer = async (url: string, model: string) => {
    await connectLocalServer(url, model);
    if (!hermes) await switchBrainProvider("local-openai");
  };

  const selection = data?.selection ?? "";
  const local: Choice[] = (data?.servers ?? []).flatMap((server) => {
    const models = server.models.length ? server.models : [server.hermes_model].filter(Boolean);
    if (models.length === 0) {
      return [{
        key: server.url,
        selection: `none:${server.url}`,
        title: server.kind,
        meta: hostOf(server.url),
        state: server.state,
        pick: () => chooseServer(server.url, ""),
      }];
    }
    return models.map((model) => ({
      key: `${server.url}|${model}`,
      selection: localSelection(hermes, server, model),
      title: model,
      meta: `${server.kind} · ${hostOf(server.url)}`,
      state: server.state,
      pick: () => chooseServer(server.url, model),
    }));
  });

  const cloudChoices: Choice[] = cloud.map((m) => ({
    key: m.id,
    selection: m.id,
    title: m.label.replace(/\s*\(Nous free\)$/, ""),
    meta: t("brain_picker.free_cloud_meta"),
    pick: () => saveBrainProviderModel("hermes", m.id),
  }));

  const autoOn = hermes && HERMES_AUTO.has(selection);

  const useHermes = () =>
    void run("brain:hermes", async () => {
      await switchBrainProvider("hermes");
      useEventStore.getState().setBrainProvider("hermes");
      window.dispatchEvent(new CustomEvent("jarvis:brain-switched"));
      setSwitched(true);
    });

  const status = hermes
    ? HERMES_AUTO.has(selection)
      ? t("brain_picker.status_hermes_auto")
      : t("brain_picker.status_hermes_model").replace("{model}", modelName(selection))
    : t("brain_picker.status_direct").replace("{provider}", known ? liveBrain : "…");

  return (
    <section data-testid="brain-picker" className="mb-6 rounded-xl border border-border bg-card/40 p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-foreground">{t("brain_picker.title")}</h3>
          <p className="mt-0.5 text-meta text-muted-foreground">
            {hermes ? t("brain_picker.subtitle_hermes") : t("brain_picker.subtitle_plain")}
          </p>
        </div>
        <button
          type="button"
          onClick={() => void scan()}
          disabled={scanning}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-md px-2 py-1 text-meta text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <RefreshCw className={cn("h-3.5 w-3.5", scanning && "animate-spin")} aria-hidden />
          {t("brain_picker.rescan")}
        </button>
      </div>

      <div
        role="radiogroup"
        aria-label={t("brain_picker.who_label")}
        data-testid="brain-switch"
        className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2"
      >
        <BrainChoice
          icon={<Sparkles className="h-5 w-5" aria-hidden />}
          title={t("brain_picker.hermes_title")}
          meta={t("brain_picker.hermes_meta")}
          on={hermes}
          busy={busy === "brain:hermes"}
          onPick={hermes ? undefined : useHermes}
        />
        <BrainChoice
          icon={<Bot className="h-5 w-5" aria-hidden />}
          title={t("brain_picker.direct_title")}
          meta={t("brain_picker.direct_meta")}
          on={!hermes}
          busy={false}
          onPick={onPickOther}
        />
      </div>
      <p data-testid="brain-status" role="status" className="mt-2 text-meta text-foreground">
        <span className={cn("mr-1.5 inline-block h-2 w-2 rounded-full", hermes ? "bg-emerald-500" : "bg-sky-400")} aria-hidden />
        {status}
        {switched && hermes && <span className="ml-1 text-muted-foreground">{t("brain_picker.switched_note")}</span>}
      </p>

      <div role="radiogroup" aria-label={t("brain_picker.title")} className="mt-4 space-y-4">
        {hermes && (
          <Row
            icon={<Sparkles className="h-4 w-4" aria-hidden />}
            title={t("brain_picker.auto_title")}
            meta={t("brain_picker.auto_meta")}
            on={autoOn}
            busy={busy === "auto"}
            onPick={() => void run("auto", () => saveBrainProviderModel("hermes", "hermes-agent"))}
          />
        )}

        <Group label={t("brain_picker.group_local")} icon={<Cpu className="h-3.5 w-3.5" aria-hidden />}>
          {data === null && scanning && (
            <p className="px-3 py-2 text-meta text-muted-foreground">{t("brain_picker.scanning")}</p>
          )}
          {data !== null && local.length === 0 && (
            <p data-testid="brain-picker-none" className="px-3 py-2 text-meta text-muted-foreground">
              {t("brain_picker.none_found")}
            </p>
          )}
          {local.map((c) => (
            <Row
              key={c.key}
              icon={<Cpu className="h-4 w-4" aria-hidden />}
              title={c.title}
              meta={c.meta}
              state={c.state}
              on={c.selection === selection}
              busy={busy === c.key}
              disabled={c.state === "off" && !c.selection.includes("::")}
              onPick={() => void run(c.key, c.pick)}
            />
          ))}
          {local.some((c) => c.state === "off") && (
            <p data-testid="brain-picker-off-hint" className="px-3 pt-1 text-meta text-muted-foreground">
              {t("brain_picker.off_hint")}
            </p>
          )}
          {adding ? (
            <form
              className="flex items-center gap-2 px-3 py-2"
              onSubmit={(e) => {
                e.preventDefault();
                if (!address.trim()) return;
                void run("add", () => chooseServer(address.trim(), "")).then(() => setAdding(false));
              }}
            >
              <input
                autoFocus
                value={address}
                onChange={(e) => setAddress(e.target.value)}
                placeholder="127.0.0.1:11437"
                aria-label={t("brain_picker.add_label")}
                className="h-8 min-w-0 flex-1 rounded-md border border-border bg-background px-2 text-sm"
              />
              <button
                type="submit"
                disabled={busy === "add"}
                className="h-8 rounded-md bg-primary px-3 text-meta font-medium text-primary-foreground disabled:opacity-60"
              >
                {busy === "add" ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : t("brain_picker.add_connect")}
              </button>
            </form>
          ) : (
            <button
              type="button"
              onClick={() => setAdding(true)}
              className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-meta text-muted-foreground hover:bg-muted hover:text-foreground"
            >
              <Plus className="h-3.5 w-3.5" aria-hidden />
              {t("brain_picker.add")}
            </button>
          )}
        </Group>

        {hermes && cloudChoices.length > 0 && (
          <Group label={t("brain_picker.group_cloud")} icon={<Cloud className="h-3.5 w-3.5" aria-hidden />}>
            {cloudChoices.map((c) => (
              <Row
                key={c.key}
                icon={<Cloud className="h-4 w-4" aria-hidden />}
                title={c.title}
                meta={c.meta}
                on={c.selection === selection}
                busy={busy === c.key}
                onPick={() => void run(c.key, c.pick)}
              />
            ))}
          </Group>
        )}
      </div>

      {error && (
        <p role="alert" className="mt-3 text-meta text-red-400">
          {error}
        </p>
      )}
    </section>
  );
}

/** One of the two big "who answers" choices, with an "Active" badge when on. */
function BrainChoice({
  icon,
  title,
  meta,
  on,
  busy,
  onPick,
}: {
  icon: React.ReactNode;
  title: string;
  meta: string;
  on: boolean;
  busy: boolean;
  onPick?: () => void;
}) {
  const t = useT();
  return (
    <button
      type="button"
      role="radio"
      aria-checked={on}
      disabled={busy}
      onClick={onPick}
      className={cn(
        "flex items-start gap-3 rounded-xl border-2 p-3 text-left transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        on ? "border-primary bg-primary/10" : "border-border hover:bg-muted",
      )}
    >
      <span className={on ? "text-primary" : "text-muted-foreground"}>{icon}</span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-2 text-sm font-semibold text-foreground">
          {title}
          {on && (
            <span className="rounded-full bg-primary px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-primary-foreground">
              {t("brain_picker.active")}
            </span>
          )}
        </span>
        <span className="mt-0.5 block text-meta text-muted-foreground">{meta}</span>
      </span>
      {busy && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" aria-hidden />}
    </button>
  );
}

function Group({ label, icon, children }: { label: string; icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-1 flex items-center gap-1.5 px-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        {icon}
        {label}
      </div>
      <div className="space-y-1">{children}</div>
    </div>
  );
}

function Row({
  icon,
  title,
  meta,
  state,
  on,
  busy,
  disabled = false,
  onPick,
}: {
  icon: React.ReactNode;
  title: string;
  meta: string;
  state?: LocalServer["state"];
  on: boolean;
  busy: boolean;
  disabled?: boolean;
  onPick: () => void;
}) {
  const t = useT();
  return (
    <button
      type="button"
      role="radio"
      aria-checked={on}
      disabled={disabled || busy}
      onClick={onPick}
      className={cn(
        "flex w-full items-center gap-3 rounded-lg border px-3 py-2 text-left transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        on ? "border-primary/60 bg-primary/10" : "border-transparent hover:bg-muted",
        disabled && "cursor-not-allowed opacity-60",
      )}
    >
      <span className="text-muted-foreground">{icon}</span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium text-foreground">{title}</span>
        <span className="block truncate text-meta text-muted-foreground">{meta}</span>
      </span>
      {state && <StateChip state={state} />}
      <span className="flex h-5 w-5 items-center justify-center">
        {busy ? (
          <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" aria-hidden />
        ) : on ? (
          <Check className="h-4 w-4 text-primary" aria-label={t("brain_picker.selected")} />
        ) : null}
      </span>
    </button>
  );
}

function StateChip({ state }: { state: LocalServer["state"] }) {
  const t = useT();
  const tone =
    state === "running" ? "bg-emerald-500" : state === "loading" ? "bg-amber-400" : "bg-muted-foreground/50";
  return (
    <span className="inline-flex shrink-0 items-center gap-1.5 text-meta text-muted-foreground">
      <span className={cn("h-2 w-2 rounded-full", tone)} aria-hidden />
      {t(`brain_picker.state_${state}`)}
    </span>
  );
}
