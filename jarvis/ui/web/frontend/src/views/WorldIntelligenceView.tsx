import { useEffect, useRef, useState } from "react";
import { ExternalLink, Globe2 } from "lucide-react";
import {
  clearPendingGeVView,
  readPendingGeVView,
} from "@/lib/gevViewBridge";
import { StarkClock } from "@/components/stark/StarkInstruments";
import { StarkPanel } from "@/components/stark/StarkPanel";
import { NewsBroadcast, NewsFeeds, NewsTicker } from "@/components/stark/WorldPanels";
import { useT } from "@/i18n";

/** The local GEV server is the one globe runtime controlled by the GEV MCP. */
export const DEFAULT_GEV_EMBED_URL = "http://127.0.0.1:4173/";

/** Only loopback GEV servers can be embedded; reject remote origins. */
export function resolveGeVEmbedUrl(configured?: string): string | null {
  try {
    const url = new URL(configured || DEFAULT_GEV_EMBED_URL);
    if (
      !["http:", "https:"].includes(url.protocol) ||
      !["127.0.0.1", "localhost", "[::1]"].includes(url.hostname) ||
      url.username ||
      url.password
    )
      return null;
    url.searchParams.set("embed", "1");
    return url.href;
  } catch {
    return null;
  }
}

export const GEV_EMBED_URL = resolveGeVEmbedUrl(
  import.meta.env.VITE_GEV_EMBED_URL,
);

export function WorldIntelligenceView() {
  const t = useT();
  const frameRef = useRef<HTMLIFrameElement>(null);
  const [globeStatus, setGlobeStatus] = useState("Connecting to globe");

  useEffect(() => {
    if (!GEV_EMBED_URL) return;
    const origin = new URL(GEV_EMBED_URL).origin;
    let ready = false;
    let queuedView: Record<string, unknown> | null = readPendingGeVView();
    let activeRequest: string | null = null;
    let timeout: number | undefined;
    let sequence = 0;

    const sendView = (view: Record<string, unknown>) => {
      const target = frameRef.current?.contentWindow;
      if (!ready || !target) {
        queuedView = view;
        return;
      }
      const id = `jarvis-gev-${Date.now()}-${++sequence}`;
      activeRequest = id;
      clearPendingGeVView();
      setGlobeStatus("Applying requested view");
      target.postMessage({ type: "gev:view", id, view }, origin);
      window.clearTimeout(timeout);
      timeout = window.setTimeout(() => {
        if (activeRequest === id)
          setGlobeStatus("The globe did not confirm the requested view");
      }, 15_000);
    };

    const onMessage = (event: MessageEvent) => {
      if (
        event.source !== frameRef.current?.contentWindow ||
        event.origin !== origin ||
        !event.data ||
        typeof event.data !== "object"
      )
        return;
      if (event.data.type === "gev:ready") {
        ready = true;
        setGlobeStatus("Globe connected");
        if (queuedView) {
          const view = queuedView;
          queuedView = null;
          sendView(view);
        }
      } else if (
        event.data.type === "gev:view-applied" &&
        event.data.id === activeRequest
      ) {
        window.clearTimeout(timeout);
        activeRequest = null;
        setGlobeStatus(
          event.data.ok === true ? "View applied" : "The globe could not apply this view",
        );
      }
    };

    const onRequestedView = (event: Event) => {
      const view = (event as CustomEvent<{ view?: unknown }>).detail?.view;
      if (
        view &&
        typeof view === "object" &&
        !Array.isArray(view) &&
        "camera" in view &&
        view.camera &&
        typeof view.camera === "object"
      )
        sendView(view as Record<string, unknown>);
    };

    window.addEventListener("message", onMessage);
    window.addEventListener("jarvis:gev-view", onRequestedView);
    return () => {
      window.removeEventListener("message", onMessage);
      window.removeEventListener("jarvis:gev-view", onRequestedView);
      window.clearTimeout(timeout);
    };
  }, []);

  if (!GEV_EMBED_URL) {
    return (
      <section className="flex h-full min-h-0 min-w-0 flex-col items-center justify-center gap-3 bg-background p-8 text-center">
        <Globe2 className="h-8 w-8 text-muted-foreground" aria-hidden="true" />
        <h1 className="text-heading">World Intelligence is unavailable</h1>
        <p className="max-w-lg text-body text-muted-foreground">
          Configure God’s Eye View to use a local loopback address before embedding it in JARVIS.
        </p>
      </section>
    );
  }
  return (
    <section className="stark-hud flex h-full min-h-0 min-w-0 flex-col" data-testid="stark-world">
      <header className="relative flex shrink-0 flex-wrap items-center gap-x-5 gap-y-2 px-4 py-2.5">
        <Globe2 className="h-6 w-6 shrink-0 text-primary" aria-hidden="true" />
        <div className="flex min-w-0 flex-col leading-none">
          <h1 className="stark-title truncate font-mono text-xl font-bold uppercase tracking-[0.32em] text-primary">
            {t("stark.world.title")}
          </h1>
          <p
            aria-live="polite"
            className="mt-1 truncate font-mono text-[9px] uppercase tracking-[0.24em] text-muted-foreground"
          >
            God’s Eye View · {globeStatus}
          </p>
        </div>
        <div className="ml-auto flex items-center gap-4">
          <a
            className="stark-tile inline-flex shrink-0 items-center gap-2 px-3 py-1.5 font-mono text-[10px] uppercase tracking-[0.16em] text-muted-foreground hover:text-foreground"
            href={GEV_EMBED_URL}
            target="_blank"
            rel="noopener noreferrer"
          >
            Open separately
            <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
          </a>
          <StarkClock />
        </div>
      </header>
      <div className="grid min-h-0 flex-1 gap-3 px-3 pb-3 xl:grid-cols-[1fr_minmax(300px,24rem)]">
        <StarkPanel
          index="06"
          title="God’s Eye View"
          className="min-h-[360px]"
          bodyClassName="flex px-1.5 pb-1.5"
          testId="stark-gev"
        >
          <iframe
            ref={frameRef}
            className="min-h-0 min-w-0 flex-1 border-0 bg-black"
            title="God’s Eye View"
            src={GEV_EMBED_URL}
            loading="eager"
            referrerPolicy="no-referrer"
            allow="microphone; fullscreen"
            allowFullScreen
          />
        </StarkPanel>
        <div className="flex min-h-0 flex-col gap-3">
          <NewsBroadcast className="min-h-[240px] flex-1" />
          <NewsFeeds className="shrink-0" />
        </div>
      </div>
      <div className="shrink-0 px-3 pb-3">
        <NewsTicker />
      </div>
    </section>
  );
}
