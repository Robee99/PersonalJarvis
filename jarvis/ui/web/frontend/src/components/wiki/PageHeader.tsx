/**
 * Wiki page header: where the page lives, its title, the one line of facts
 * that matter (kind, last change, length), the frontmatter worth showing, and
 * the hand-off to Obsidian.
 */
import { lazy, Suspense } from "react";
import { ExternalLink } from "lucide-react";

import { fill, useT, useUiLanguage } from "@/i18n";
import type { OriginalConversation } from "@/lib/importedSources";
import type { WikiKind } from "@/lib/wikiApi";
import { GROUP_LABEL_KEY, groupOfKind, relativeAge } from "@/lib/wikiModel";
import { cn } from "@/lib/utils";
import { KindGlyph } from "@/components/wiki/KindGlyph";

// Lazy so the page body paints before the Obsidian hand-off code arrives.
const ObsidianButton = lazy(() =>
  import("./ObsidianButton").then((mod) => ({
    default: mod.ObsidianButton,
  })),
);

interface PageHeaderProps {
  slug: string;
  kind: WikiKind | string;
  title: string;
  frontmatter: Record<string, string | string[]>;
  vaultRoot: string;
  vaultRelPath: string;
  /** Seconds since the epoch of the file's last change, when known. */
  mtime?: number;
  words?: number;
  /** Imported ChatGPT / Claude conversations on this page, linked to the original. */
  originals?: OriginalConversation[];
}

/** How many original-conversation links the header lists before "+N more". */
const MAX_ORIGINALS = 4;

const FRIENDLY_LABELS: Record<string, string> = {
  type: "type",
  entity_kind: "kind",
  status: "status",
  created: "created",
  updated: "updated",
  started: "started",
  last_activity: "last activity",
};

const MAX_PILLS = 6;

export function PageHeader({
  slug,
  kind,
  title,
  frontmatter,
  vaultRoot,
  vaultRelPath,
  mtime,
  words,
  originals = [],
}: PageHeaderProps) {
  const t = useT();
  const language = useUiLanguage();
  const pills = buildPills(frontmatter);
  const breadcrumb = breadcrumbFromPath(vaultRelPath);
  const group = groupOfKind(kind);
  const changed = mtime ? relativeAge(mtime, language) : "";

  return (
    <header className="flex flex-col" data-testid="wiki-page-header" data-slug={slug}>
      <div className="flex items-center justify-between gap-4">
        <nav
          className="flex min-w-0 items-center gap-1.5 text-sm text-foreground-faint"
          data-testid="wiki-page-crumb"
          aria-label={t("wiki_ui.crumb_label")}
        >
          {breadcrumb.map((part, idx) => (
            <span key={idx} className="flex min-w-0 items-center gap-1.5">
              {idx > 0 && <span aria-hidden>/</span>}
              <span className={cn("truncate", idx === breadcrumb.length - 1 && "text-muted-foreground")}>
                {part}
              </span>
            </span>
          ))}
        </nav>
        <Suspense fallback={<ObsidianButtonPlaceholder />}>
          <ObsidianButton vaultRoot={vaultRoot} vaultRelPath={vaultRelPath} size="sm" />
        </Suspense>
      </div>

      <h1
        className="mt-5 text-display font-semibold text-foreground-strong"
        data-testid="wiki-page-title"
      >
        {title}
      </h1>

      <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
        <span className="inline-flex items-center gap-1.5 text-foreground-secondary">
          <KindGlyph group={group} className="h-2.5 w-2.5" />
          {t(GROUP_LABEL_KEY[group])}
        </span>
        {changed && (
          <>
            <span aria-hidden className="text-foreground-faint">·</span>
            <span>{t("wiki_ui.page_changed").replace("{0}", changed)}</span>
          </>
        )}
        {words !== undefined && words > 0 && (
          <>
            <span aria-hidden className="text-foreground-faint">·</span>
            <span>
              {t("wiki_ui.page_words").replace("{0}", new Intl.NumberFormat(language).format(words))}
            </span>
          </>
        )}
      </div>

      {/* The page kind used to be painted in one of four hardcoded hues. Hue
          belongs to status and selection only, so the frontmatter facts are
          neutral tags and the kind is told by its shape above. */}
      {originals.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm" data-testid="wiki-page-originals">
          <span className="text-muted-foreground">{t("wiki_ui.original_label")}</span>
          {originals.slice(0, MAX_ORIGINALS).map((o) => (
            <a
              key={o.id}
              href={o.url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-primary hover:underline"
              data-testid="wiki-page-original"
            >
              {t(`wiki_ui.original_${o.format}`)}
              <ExternalLink className="h-3.5 w-3.5" aria-hidden />
            </a>
          ))}
          {originals.length > MAX_ORIGINALS && (
            <span className="text-muted-foreground">{fill(t("wiki_ui.original_more"), { count: originals.length - MAX_ORIGINALS })}</span>
          )}
        </div>
      )}

      {pills.length > 0 && (
        <div className="mt-4 flex flex-wrap gap-1.5 text-xs" data-testid="wiki-page-pills">
          {pills.map((p) => (
            <span
              key={p.key}
              className="inline-flex h-6 items-center gap-1 rounded-sm border border-border px-2 text-muted-foreground"
              data-pill-key={p.key}
            >
              <span>{p.label}</span>
              <span className="text-foreground">{p.value}</span>
            </span>
          ))}
        </div>
      )}
    </header>
  );
}

interface Pill {
  key: string;
  label: string;
  value: string;
}

function buildPills(fm: Record<string, string | string[]>): Pill[] {
  const out: Pill[] = [];
  for (const [key, raw] of Object.entries(fm)) {
    if (key === "slug" || key === "aliases") continue;
    if (!(key in FRIENDLY_LABELS)) continue;
    const value = Array.isArray(raw) ? raw.join(", ") : raw;
    if (!value || value.trim() === "") continue;
    out.push({ key, label: FRIENDLY_LABELS[key], value });
    if (out.length >= MAX_PILLS) break;
  }
  return out;
}

function breadcrumbFromPath(relPath: string): string[] {
  // "entities/harald.md" → ["entities", "harald.md"]
  const parts = relPath.split(/[\\/]/).filter(Boolean);
  return parts.length > 0 ? parts : [relPath];
}

function ObsidianButtonPlaceholder() {
  return <span className="h-8 w-36 shrink-0" data-testid="obsidian-button-placeholder" aria-hidden />;
}
