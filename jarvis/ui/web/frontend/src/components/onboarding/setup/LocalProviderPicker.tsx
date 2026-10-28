import { Check, Cpu, Loader2, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { switchBrainProvider } from "@/hooks/useProviders";
import { listLocalServers, useLocalServer, type LocalServer } from "@/hooks/localServers";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";

function hostOf(url: string): string {
  return url.replace(/^https?:\/\//, "");
}

function serverStateText(state: LocalServer["state"], t: ReturnType<typeof useT>): string {
  return t(`first_run.connect.local_${state}`);
}

/** A first-run path for local OpenAI-compatible servers. This deliberately
 * selects Jarvis's local brain directly, even when Hermes is the current brain. */
export function LocalProviderPicker({
  active = false,
  activeUrl,
  onConnected,
  onChanged,
}: {
  active?: boolean;
  activeUrl?: string | null;
  onConnected: () => void;
  onChanged: () => void;
}) {
  const t = useT();
  const [servers, setServers] = useState<LocalServer[]>([]);
  const [selection, setSelection] = useState("");
  const [scanning, setScanning] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  const scan = useCallback(async () => {
    setScanning(true);
    setError(null);
    try {
      const result = await listLocalServers();
      setServers(result.servers);
      setSelection(result.selection);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setScanning(false);
    }
  }, []);

  useEffect(() => {
    void scan();
  }, [scan]);

  const choose = async (server: LocalServer, model: string) => {
    const key = `${server.url}|${model}`;
    setBusy(key);
    setError(null);
    try {
      await useLocalServer(server.url, model, true);
      await switchBrainProvider("local-openai");
      useEventStore.getState().setBrainProvider("local-openai");
      window.dispatchEvent(new CustomEvent("jarvis:brain-switched"));
      setSelected(key);
      onConnected();
      onChanged();
      await scan();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const normalizedActiveUrl = activeUrl?.replace(/\/$/, "");
  return (
    <section
      className="mb-2 rounded-lg border border-border bg-card/40 p-3"
      data-testid="setup-local-provider"
      aria-label={t("first_run.connect.local_title")}
    >
      <div className="flex items-start gap-3">
        <Cpu aria-hidden className="mt-0.5 h-5 w-5 shrink-0 text-primary" />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-sm font-medium text-foreground">{t("first_run.connect.local_title")}</p>
            {active && (
              <span className="inline-flex items-center gap-1 text-xs font-medium text-success">
                <Check aria-hidden className="h-3.5 w-3.5" />
                {t("first_run.connect.ready")}
              </span>
            )}
          </div>
          <p className="mt-0.5 text-xs text-muted-foreground">{t("first_run.connect.local_lede")}</p>
        </div>
        <button
          type="button"
          onClick={() => void scan()}
          disabled={scanning}
          aria-label={t("first_run.connect.local_rescan")}
          className="inline-flex shrink-0 items-center gap-1 rounded px-2 py-1 text-xs text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-60"
        >
          <RefreshCw aria-hidden className={cn("h-3.5 w-3.5", scanning && "animate-spin")} />
          {t("first_run.connect.local_rescan")}
        </button>
      </div>

      <div className="mt-2 space-y-1">
        {scanning && servers.length === 0 && (
          <p className="px-2 py-1 text-xs text-muted-foreground" role="status">
            {t("first_run.connect.local_scanning")}
          </p>
        )}
        {!scanning && !error && servers.length === 0 && (
          <p className="px-2 py-1 text-xs text-muted-foreground" data-testid="setup-local-none">
            {t("first_run.connect.local_none")}
          </p>
        )}
        {servers.map((server) => {
          const models = server.models.length ? server.models : [server.hermes_model].filter(Boolean);
          if (models.length === 0) {
            return (
              <p key={server.url} className="flex items-center justify-between gap-2 rounded-md px-2 py-2 text-xs text-muted-foreground">
                <span className="min-w-0 truncate">{server.kind} · {hostOf(server.url)}</span>
                <span className="shrink-0">{serverStateText(server.state, t)}</span>
              </p>
            );
          }
          return models.map((model) => {
            const key = `${server.url}|${model}`;
            const on = selected === key || (
              active && (selection ? selection === key : normalizedActiveUrl === server.url.replace(/\/$/, ""))
            );
            const blocked = server.state !== "running";
            return (
              <button
                key={key}
                type="button"
                disabled={blocked || busy !== null}
                onClick={() => void choose(server, model)}
                data-testid={`setup-local-model-${server.port}-${model}`}
                className={cn(
                  "flex w-full items-center gap-3 rounded-md border px-2.5 py-2 text-left transition-colors",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  on ? "border-primary/60 bg-primary/10" : "border-transparent hover:bg-muted",
                  blocked && "cursor-not-allowed opacity-60",
                )}
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium text-foreground">{model}</span>
                  <span className="block truncate text-xs text-muted-foreground">{server.kind} · {hostOf(server.url)}</span>
                </span>
                <span className="shrink-0 text-xs text-muted-foreground">{serverStateText(server.state, t)}</span>
                {busy === key ? (
                  <Loader2 aria-hidden className="h-4 w-4 animate-spin" />
                ) : on ? (
                  <Check aria-label={t("first_run.connect.ready")} className="h-4 w-4 text-primary" />
                ) : (
                  <span className="shrink-0 text-xs font-medium text-primary">{t("first_run.connect.local_use")}</span>
                )}
              </button>
            );
          });
        })}
      </div>
      {error && <p role="alert" className="mt-2 px-2 text-xs text-destructive">{error}</p>}
    </section>
  );
}
