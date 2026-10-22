/**
 * Where an imported chat came from. The wiki importer writes a marker in
 * front of each imported conversation
 * (`<!-- jarvis-conversation id="…" format="chatgpt|claude|messages" … -->`,
 * `jarvis/memory/wiki/import_formats.py`); a page made from a ChatGPT or
 * Claude export can link each conversation back to the original.
 *
 * An id the importer made up (`derived-…`), or a format with no web address
 * of its own, has no original to link to and is left out.
 */
export interface OriginalConversation {
  id: string;
  format: "chatgpt" | "claude";
  url: string;
}

const MARKER_RE = /<!-- jarvis-conversation((?: [a-z_]+="[^"]*")*) -->/g;
const ATTR_RE = /([a-z_]+)="([^"]*)"/g;
const SAFE_ID = /^[A-Za-z0-9-]{8,80}$/;

const ORIGIN: Record<OriginalConversation["format"], (id: string) => string> = {
  chatgpt: (id) => `https://chatgpt.com/c/${id}`,
  claude: (id) => `https://claude.ai/chat/${id}`,
};

export function originalConversations(body: string | undefined | null): OriginalConversation[] {
  if (!body) return [];
  const out: OriginalConversation[] = [];
  const seen = new Set<string>();
  for (const marker of body.matchAll(MARKER_RE)) {
    const attrs: Record<string, string> = {};
    for (const [, key, value] of marker[1].matchAll(ATTR_RE)) attrs[key] = value;
    const id = attrs.id ?? "";
    const format = attrs.format;
    if (format !== "chatgpt" && format !== "claude") continue;
    if (!SAFE_ID.test(id) || id.startsWith("derived-") || seen.has(id)) continue;
    seen.add(id);
    out.push({ id, format, url: ORIGIN[format](id) });
  }
  return out;
}

const ANY_MARKER_RE = /^[ \t]*<!-- jarvis-(?:conversation|turn)(?: [a-z_]+="[^"]*")* -->[ \t]*(?:\r?\n|$)/gm;

/**
 * The page without the importer's markers. They are provenance for the brain
 * map, not text: left in, a marker next to a line of text is printed as is.
 */
export function stripImportMarkers(body: string): string {
  return body.replace(ANY_MARKER_RE, "");
}
