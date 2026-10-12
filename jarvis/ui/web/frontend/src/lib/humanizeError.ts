/**
 * Turn a raw infrastructure error into one short sentence a person can act on.
 *
 * The UI used to print backend and CLI output verbatim — TLS failures, stack
 * traces, "is `hermes gateway` running?", CDN module-import errors (forensic UI
 * audit 2026-10-07, P1-8). The raw text is still useful to whoever debugs it,
 * so it is kept behind a "Details" toggle (`components/ui/HumanError.tsx`);
 * what is shown first is the sentence this function picks.
 *
 * Classification is by the error's shape, never by a provider name (AP-21).
 * Unknown errors get a generic sentence: an unrecognised string is never
 * promoted to the headline.
 */

export type ErrorKind =
  | "network"
  | "tls"
  | "timeout"
  | "auth"
  | "rate_limit"
  | "not_found"
  | "service_down"
  | "load_failed"
  | "unknown";

const RULES: readonly { kind: ErrorKind; pattern: RegExp }[] = [
  { kind: "tls", pattern: /\b(ssl|tls|certificate|cert(ificate)?_verify|x509)\b/i },
  { kind: "timeout", pattern: /\b(timed? ?out|timeout|deadline exceeded|ETIMEDOUT)\b/i },
  {
    kind: "auth",
    pattern: /\b(401|403|unauthori[sz]ed|forbidden|invalid (api )?key|api key|not logged in|login required|permission denied)\b/i,
  },
  { kind: "rate_limit", pattern: /\b(429|rate[- ]?limit(ed)?|too many requests|quota)\b/i },
  {
    kind: "load_failed",
    pattern: /(failed to fetch dynamically imported module|importing a module script failed|error loading dynamically imported module|ChunkLoadError|Loading chunk \S+ failed)/i,
  },
  {
    kind: "service_down",
    pattern: /\b(gateway|is .+ running\??|503|502|service unavailable|bad gateway|not running)\b/i,
  },
  {
    kind: "network",
    pattern: /\b(ECONNREFUSED|ECONNRESET|ENOTFOUND|EAI_AGAIN|connection (refused|reset|error|failed)|network ?error|failed to fetch|unreachable|getaddrinfo)\b/i,
  },
  { kind: "not_found", pattern: /\b(404|not found|no such file|ENOENT)\b/i },
];

export function classifyError(raw: string | null | undefined): ErrorKind {
  const text = (raw ?? "").trim();
  if (!text) return "unknown";
  for (const rule of RULES) if (rule.pattern.test(text)) return rule.kind;
  return "unknown";
}

/** i18n key of the sentence shown for an error kind (`errors_human.*`). */
export function humanErrorKey(kind: ErrorKind): string {
  return `errors_human.${kind}`;
}

/**
 * Is this text already a sentence for people rather than machine output?
 * Short, one line, no stack frames, paths, hashes or code fences. Such text
 * is shown as-is; anything else is replaced by the classified sentence.
 */
export function looksHuman(raw: string | null | undefined): boolean {
  const text = (raw ?? "").trim();
  if (!text || text.length > 140) return false;
  if (/\n/.test(text)) return false;
  if (/(Traceback|^\s*at\s|\bFile "|Error:|Exception|errno|0x[0-9a-f]{6,}|[A-Za-z]:\\|\/[\w.-]+\/[\w.-]+|`|[{}[\]]|[0-9a-f]{16,})/i.test(text)) {
    return false;
  }
  return true;
}
