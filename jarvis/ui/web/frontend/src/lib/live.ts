import { useQuery } from "@tanstack/react-query";

/**
 * Live news channels and city cameras (`/api/intel/live`, from
 * `jarvis/intel/live.py`). Each one plays in YouTube's own embedded player
 * from the privacy-enhanced host. A channel that runs several live streams
 * at once is resolved to one video by the backend (`/api/intel/live/now`).
 */
export interface LiveStream {
  key: string;
  name: string;
  /** A shorter name for a channel button. */
  short: string;
  kind: "news" | "camera";
  group: string;
  place: string;
  /** The path after `/embed/`: a video id, or `live_stream?channel=…`. */
  embed: string;
  /** The stream's page on YouTube. */
  page: string;
}

export interface LiveGroup {
  id: string;
  name: string;
}

export interface LiveCatalog {
  streams: LiveStream[];
  /** Camera regions. */
  groups: LiveGroup[];
  /** News groups ("world", "india"). */
  news_groups?: LiveGroup[];
}

export const YOUTUBE_ORIGIN = "https://www.youtube-nocookie.com";

async function fetchCatalog(): Promise<LiveCatalog> {
  const res = await fetch("/api/intel/live");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as LiveCatalog;
}

export function useLiveCatalog() {
  return useQuery<LiveCatalog>({
    queryKey: ["intel", "live"],
    queryFn: fetchCatalog,
    staleTime: Infinity,
    retry: 1,
  });
}

/** Whether a stream plays a whole channel rather than one pinned video. */
export function playsChannel(stream: LiveStream): boolean {
  return stream.embed.startsWith("live_stream?");
}

/**
 * The video a channel's live page points at now, or null. Asked only for a
 * stream that plays a whole channel, and only while it is on screen.
 */
export function useNowPlaying(stream: LiveStream | undefined, enabled = true) {
  const wanted = !!stream && enabled && playsChannel(stream);
  return useQuery<string | null>({
    queryKey: ["intel", "live", "now", stream?.key ?? ""],
    queryFn: async () => {
      const res = await fetch(`/api/intel/live/now/${encodeURIComponent(stream!.key)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const body = (await res.json()) as { video?: unknown };
      return typeof body.video === "string" && /^[A-Za-z0-9_-]{11}$/.test(body.video) ? body.video : null;
    },
    enabled: wanted,
    staleTime: 10 * 60_000,
    retry: 0,
  });
}

/**
 * The player address for a stream. It always starts muted: a news channel's
 * sound would otherwise reach the microphone and talk over the assistant.
 * `video` replaces a channel embed with the video it resolved to.
 */
export function embedUrl(stream: LiveStream, autoplay: boolean, origin = window.location.origin, video?: string | null): string {
  const params = new URLSearchParams({
    autoplay: autoplay ? "1" : "0",
    mute: "1",
    playsinline: "1",
    rel: "0",
    enablejsapi: "1",
    origin,
  });
  const path = video || stream.embed;
  const sep = path.includes("?") ? "&" : "?";
  return `${YOUTUBE_ORIGIN}/embed/${path}${sep}${params.toString()}`;
}

/**
 * Whether a message from a YouTube player says it cannot play (the stream
 * ended, was made private, or the channel is not live). The player posts
 * JSON strings once it has been told the page is listening.
 */
export function isPlayerError(data: unknown): boolean {
  if (typeof data !== "string" || data.length > 4096) return false;
  try {
    const parsed = JSON.parse(data) as { event?: unknown };
    return parsed.event === "onError";
  } catch {
    // Not a player message (another frame's traffic); nothing to report.
    return false;
  }
}

/** A still of a stream for a poster before it plays (YouTube's own thumbnail). */
export function posterUrl(video: string): string {
  return `https://i.ytimg.com/vi/${encodeURIComponent(video)}/hqdefault.jpg`;
}
