import { useQuery } from "@tanstack/react-query";
import { ExternalLink, Radio } from "lucide-react";
import { StarkLamp, StarkPanel } from "@/components/stark/StarkPanel";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

/**
 * The World screen's instruments round the God's Eye View globe: the news
 * broadcast and its ticker (free public RSS through `/api/deck/news`) and the
 * feeds that answered. The globe keeps its own layer and camera controls.
 */
export interface NewsItem {
  title: string;
  link: string;
  source: string;
  /** Unix seconds, or null when the feed gave no date. */
  published: number | null;
}

export interface NewsBody {
  items: NewsItem[];
  sources: string[];
  failed: string[];
  fetched_at: number;
}

async function fetchNews(): Promise<NewsBody> {
  const res = await fetch("/api/deck/news");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as NewsBody;
}

export function useDeckNews() {
  return useQuery<NewsBody>({
    queryKey: ["deck", "news"],
    queryFn: fetchNews,
    refetchInterval: 5 * 60_000,
    staleTime: 4 * 60_000,
    retry: 1,
  });
}

/** "now", "12m", "3h", "2d": how long ago a story was published. */
export function ago(published: number | null, now = Date.now() / 1000): string {
  if (published == null) return "—";
  const s = Math.max(0, now - published);
  if (s < 60) return "now";
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86_400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86_400)}d`;
}

/** 07 — NEWS BROADCAST: the newest headlines, each opening its own story. */
export function NewsBroadcast({ className }: { className?: string }) {
  const t = useT();
  const q = useDeckNews();
  const items = q.data?.items ?? [];
  return (
    <StarkPanel
      index="07"
      title={t("stark.world.news")}
      meta={
        items.length ? (
          <span className="flex items-center gap-1.5">
            <StarkLamp state="on" />
            {t("stark.world.live")}
          </span>
        ) : undefined
      }
      className={className}
      testId="stark-news"
      bodyClassName="overflow-y-auto"
    >
      {!items.length ? (
        <p className="text-xs text-muted-foreground">
          {t(q.isError || q.data ? "stark.world.news_error" : "stark.world.news_loading")}
        </p>
      ) : (
        <ol className="space-y-2">
          {items.map((item, i) => (
            <li key={item.link} data-testid="stark-news-item">
              <a
                href={item.link}
                target="_blank"
                rel="noopener noreferrer"
                className="group block border-l-2 border-primary/25 pl-2 transition-colors hover:border-primary"
              >
                <span className="flex items-center gap-2 font-mono text-[9px] uppercase tracking-[0.16em]">
                  <span className={i === 0 ? "text-[hsl(var(--stark-hot))]" : "text-primary"}>{item.source}</span>
                  <span className="text-muted-foreground">{ago(item.published)}</span>
                  <ExternalLink className="ml-auto h-2.5 w-2.5 text-muted-foreground opacity-0 group-hover:opacity-100" aria-hidden />
                </span>
                <span className="mt-0.5 block text-xs leading-snug text-foreground group-hover:text-primary">
                  {item.title}
                </span>
              </a>
            </li>
          ))}
        </ol>
      )}
    </StarkPanel>
  );
}

/** 08 — FEEDS: which free sources answered on the last read. */
export function NewsFeeds({ className }: { className?: string }) {
  const t = useT();
  const q = useDeckNews();
  const d = q.data;
  return (
    <StarkPanel
      index="08"
      title={t("stark.world.feeds")}
      meta={d ? new Date(d.fetched_at * 1000).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }) : undefined}
      className={className}
      testId="stark-feeds"
    >
      {!d ? (
        <p className="text-xs text-muted-foreground">{t(q.isError ? "stark.world.news_error" : "stark.world.news_loading")}</p>
      ) : (
        <ul className="space-y-1">
          {d.sources.map((name) => {
            const down = d.failed.includes(name);
            return (
              <li key={name} className="flex items-center gap-2" data-testid={`stark-feed-${down ? "down" : "up"}`}>
                <StarkLamp state={down ? "warn" : "on"} />
                <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-muted-foreground">{name}</span>
                <span aria-hidden className="mx-1 h-px flex-1 border-b border-dotted border-primary/20" />
                <span className={cn("font-mono text-[10px] uppercase tracking-wider", down ? "text-[hsl(var(--stark-hot))]" : "text-foreground")}>
                  {t(down ? "stark.world.feed_down" : "stark.world.feed_up")}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </StarkPanel>
  );
}

/** The broadcast ticker along the foot of the screen; it pauses under the pointer. */
export function NewsTicker() {
  const t = useT();
  const items = useDeckNews().data?.items ?? [];
  return (
    <div className="stark-panel flex h-8 shrink-0 items-center overflow-hidden" data-testid="stark-ticker">
      <span className="flex h-full shrink-0 items-center gap-1.5 bg-[hsl(var(--stark-hot)/0.15)] px-3 font-mono text-[10px] font-semibold uppercase tracking-[0.2em] text-[hsl(var(--stark-hot))]">
        <Radio className="h-3 w-3" aria-hidden />
        {t("stark.world.breaking")}
      </span>
      <div className="relative min-w-0 flex-1 overflow-hidden">
        {items.length ? (
          <div className="stark-ticker flex w-max whitespace-nowrap">
            {[0, 1].map((copy) => (
              <span key={copy} aria-hidden={copy === 1} className="flex">
                {items.slice(0, 20).map((item) => (
                  <span key={`${copy}-${item.link}`} className="px-4 font-mono text-[11px] text-foreground">
                    <span className="mr-2 text-primary">{item.source.toUpperCase()}</span>
                    {item.title}
                    <span className="ml-4 text-primary/40">◆</span>
                  </span>
                ))}
              </span>
            ))}
          </div>
        ) : (
          <span className="px-4 font-mono text-[11px] text-muted-foreground">{t("stark.world.news_loading")}</span>
        )}
      </div>
    </div>
  );
}
