import { useQuery } from "@tanstack/react-query";

/**
 * Live news channels and city cameras (`/api/intel/live`, from
 * `jarvis/intel/live.py`). Each one plays in YouTube's own embedded player
 * from the privacy-enhanced host; JARVIS sends nothing to YouTube itself.
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
  groups: LiveGroup[];
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

/**
 * The player address for a stream. It always starts muted: a news channel's
 * sound would otherwise reach the microphone and talk over the assistant.
 */
export function embedUrl(stream: LiveStream, autoplay: boolean, origin = window.location.origin): string {
  const params = new URLSearchParams({
    autoplay: autoplay ? "1" : "0",
    mute: "1",
    playsinline: "1",
    rel: "0",
    enablejsapi: "1",
    origin,
  });
  const sep = stream.embed.includes("?") ? "&" : "?";
  return `${YOUTUBE_ORIGIN}/embed/${stream.embed}${sep}${params.toString()}`;
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
