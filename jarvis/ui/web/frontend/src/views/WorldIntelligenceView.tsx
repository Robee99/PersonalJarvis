import { ExternalLink, Globe2 } from "lucide-react";

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
    <section className="flex h-full min-h-0 min-w-0 flex-col bg-background">
      <header className="flex shrink-0 items-center justify-between gap-4 border-b border-border px-5 py-3">
        <div className="flex min-w-0 items-center gap-3">
          <Globe2 className="h-5 w-5 shrink-0 text-primary" aria-hidden="true" />
          <div className="min-w-0">
            <h1 className="truncate text-heading">World Intelligence</h1>
            <p className="truncate text-meta text-muted-foreground">
              God’s Eye View · local globe and live layers
            </p>
          </div>
        </div>
        <a
          className="inline-flex shrink-0 items-center gap-2 rounded-md border border-border px-3 py-2 text-meta text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          href={GEV_EMBED_URL}
          target="_blank"
          rel="noopener noreferrer"
        >
          Open separately
          <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
        </a>
      </header>
      <iframe
        className="min-h-0 min-w-0 flex-1 border-0 bg-black"
        title="God’s Eye View"
        src={GEV_EMBED_URL}
        loading="eager"
        referrerPolicy="no-referrer"
        allow="microphone; fullscreen"
        allowFullScreen
      />
    </section>
  );
}
