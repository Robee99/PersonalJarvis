// The memory orb: one map of what the assistant remembers and what it can use,
// with an ask bar underneath.
//
// Two maps share the frame. "Projects" (components/orb/ProjectsOrb) is the
// brain map's project intelligence: health, progress and the conversation
// thread of every project. "Memory" is the map below. Hand control
// (components/orb/hands) drives whichever map is shown through the same
// handlers as the mouse; the mouse, keyboard and touch always keep working.
//
// Only real things and real links are drawn (`lib/orbGraph.ts`): notes linked
// as the vault links them, and each family (notes, skills, tools, connected
// apps, MCP servers) as a labelled region. Capabilities come from Hermes Agent
// when it is reachable, the same list the Tool Armory shows. A catalog that
// could not be read is marked unavailable, never shown as zero. Typing in the
// ask bar sends a normal chat turn (`lib/chat.ts`).
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import ForceGraph2D from "react-force-graph-2d";
import type { ForceGraphMethods, NodeObject } from "react-force-graph-2d";
import { Crosshair, Network, Orbit, Search, Send, X } from "lucide-react";

import { HandOverlay, HandToggle } from "@/components/orb/hands/HandControls";
import { graphHandController } from "@/components/orb/hands/graphController";
import { useHandTracking, type HandController, type HandDeps } from "@/components/orb/hands/useHandTracking";
import { ProjectsOrb } from "@/components/orb/ProjectsOrb";
import { fill, useT } from "@/i18n";
import { sendChatMessage } from "@/lib/chat";
import {
  ORB_GROUP_COLOUR,
  ORB_GROUPS,
  buildOrbGraph,
  connectedApps,
  createRegionForce,
  filterOrbGraph,
  itemsFromHermes,
  itemsFromJarvis,
  matchOrbNodes,
  regionAnchors,
  type HermesCatalogs,
  type JarvisCatalogs,
  type MarketplaceApp,
  type OrbGroup,
  type OrbNode,
  type OrbSources,
} from "@/lib/orbGraph";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";

import { MemoryOrbImport } from "./MemoryOrbImport";

type DrawNode = NodeObject<OrbNode> & OrbNode;

const NODE_RADIUS = 3.2;
const REGION_RADIUS = 260;
const MAX_LISTED_HITS = 8;
/** Room around a framed map for the title, the legend and the ask bar. */
const FIT_PADDING = 140;
const MODE_KEY = "orb2.mode";

export type OrbMode = "projects" | "memory";

function savedMode(): OrbMode {
  try {
    return window.localStorage.getItem(MODE_KEY) === "memory" ? "memory" : "projects";
  } catch {
    // Storage can be blocked (private window, previews); projects is the default.
    return "projects";
  }
}

async function getJson<T>(url: string): Promise<T | undefined> {
  try {
    const res = await fetch(url);
    if (!res.ok) return undefined;
    const body = (await res.json()) as T & { ok?: boolean };
    // Some routes answer 200 with `{ok: false}` when they fail.
    return body && body.ok === false ? undefined : body;
  } catch {
    // An unreachable catalog is reported as unavailable on the legend.
    return undefined;
  }
}

export async function fetchOrbSources(): Promise<OrbSources> {
  const [wiki, hermes, apps] = await Promise.all([
    getJson<OrbSources["wiki"]>("/api/wiki/graph"),
    getJson<HermesCatalogs>("/api/hermes/inventory"),
    getJson<{ plugins: MarketplaceApp[] }>("/api/marketplace/plugins"),
  ]);
  const base = { wiki: Array.isArray(wiki?.nodes) ? wiki : undefined, apps: connectedApps(apps?.plugins) };
  if (hermes?.available) {
    return { ...base, ...itemsFromHermes(hermes), capabilitySource: "hermes" };
  }
  const [skills, tools, mcp] = await Promise.all([
    getJson<{ skills: JarvisCatalogs["skills"] }>("/api/skills"),
    getJson<{ tools: JarvisCatalogs["tools"] }>("/api/tools"),
    getJson<{ servers: JarvisCatalogs["mcp"] }>("/api/mcps"),
  ]);
  return {
    ...base,
    ...itemsFromJarvis({ skills: skills?.skills, tools: tools?.tools, mcp: mcp?.servers }),
    capabilitySource: "jarvis",
  };
}

function useSize(ref: React.RefObject<HTMLDivElement>, mounted: boolean) {
  const [size, setSize] = useState({ width: 0, height: 0 });
  // `mounted` re-measures when the element appears (the map is switched to).
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    setSize({ width: el.clientWidth, height: el.clientHeight });
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      if (width > 0 && height > 0) setSize({ width, height });
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref, mounted]);
  return size;
}

function useDebounced<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const id = window.setTimeout(() => setSettled(value), ms);
    return () => window.clearTimeout(id);
  }, [value, ms]);
  return settled;
}

export function MemoryOrbView({
  handDeps,
  initialMode,
  importOpen = false,
}: {
  handDeps?: HandDeps;
  /** Open on this map instead of the one last shown (the HUD's buttons pick). */
  initialMode?: OrbMode;
  /** Open with the data import form up (the HUD memory panel's Import button). */
  importOpen?: boolean;
} = {}) {
  const t = useT();
  const [mode, setModeState] = useState<OrbMode>(() => initialMode ?? savedMode());
  const setMode = (next: OrbMode) => {
    setModeState(next);
    try {
      window.localStorage.setItem(MODE_KEY, next);
    } catch {
      // Remembering the map is a convenience; without storage it resets.
    }
  };
  // Hands drive whichever map is shown; each map registers its controller.
  const handTarget = useRef<HandController | null>(null);
  const registerHands = useCallback((controller: HandController | null) => {
    handTarget.current = controller;
  }, []);
  const handController = useMemo<HandController>(
    () => ({ onAction: (action) => handTarget.current?.onAction(action) }),
    [],
  );
  const tracking = useHandTracking(handController, handDeps);
  const wrapRef = useRef<HTMLDivElement>(null);
  const graphRef = useRef<ForceGraphMethods<DrawNode> | undefined>(undefined);
  const { width, height } = useSize(wrapRef, mode === "memory");
  const groupLabel = (group: OrbGroup) => t(`orb2.memory.group.${group}`);
  const [hidden, setHidden] = useState<Set<OrbGroup>>(new Set());
  const [query, setQuery] = useState("");
  const settledQuery = useDebounced(query, 180);
  const [hover, setHover] = useState<OrbNode | null>(null);
  const [picked, setPicked] = useState<OrbNode | null>(null);
  const [ask, setAsk] = useState("");
  const [sentAt, setSentAt] = useState<number | null>(null);
  // The map is framed again whenever what is on it changes.
  const framedFor = useRef("");

  const connected = useEventStore((s) => s.connected);
  const voiceReady = useEventStore((s) => s.voiceReady);
  const brainProvider = useEventStore((s) => s.brainProvider);
  const brainModel = useEventStore((s) => s.brainModel);
  const thinking = useEventStore((s) => s.chatThinking);
  const messages = useEventStore((s) => (sentAt === null ? null : s.messages));
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const requestWikiPage = useEventStore((s) => s.requestWikiPage);

  const { data: sources, isLoading } = useQuery({
    queryKey: ["orb", "sources"],
    queryFn: fetchOrbSources,
    staleTime: 30_000,
    enabled: mode === "memory",
  });

  const full = useMemo(() => buildOrbGraph(sources ?? {}), [sources]);
  const filtered = useMemo(() => filterOrbGraph(full, hidden), [full, hidden]);
  // The force engine mutates the objects it is given, so it gets fresh copies
  // whenever what is shown changes and keeps them otherwise.
  const shown = useMemo(
    () => ({
      nodes: filtered.nodes.map((n) => ({ ...n })) as DrawNode[],
      links: filtered.links.map((l) => ({ ...l })),
    }),
    [filtered],
  );
  const shownKey = `${full.nodes.length}|${[...hidden].sort().join(",")}`;
  const presentGroups = useMemo(
    () => ORB_GROUPS.filter((g) => filtered.nodes.some((n) => n.group === g)),
    [filtered],
  );
  const anchors = useMemo(() => regionAnchors(presentGroups, REGION_RADIUS), [presentGroups]);

  const hits = useMemo(() => matchOrbNodes(filtered, settledQuery), [filtered, settledQuery]);
  const hitIds = useMemo(() => new Set(hits.map((n) => n.id)), [hits]);
  const searching = settledQuery.trim().length > 0;

  const total = full.nodes.length;
  const reply = useMemo(() => {
    if (sentAt === null || !messages) return null;
    return [...messages].reverse().find((m) => m.role === "assistant" && m.ts >= sentAt - 1000) ?? null;
  }, [messages, sentAt]);

  useEffect(() => {
    const fg = graphRef.current;
    if (!fg) return;
    fg.d3Force("charge")?.strength(-28);
    fg.d3Force("link")?.distance(28);
    fg.d3Force("region", createRegionForce(anchors, 0.06) as never);
    fg.d3ReheatSimulation();
  }, [shown, anchors]);

  const frame = (onlyHits: boolean) => {
    const fg = graphRef.current;
    if (!fg || !shown.nodes.length) return;
    if (onlyHits && hitIds.size) fg.zoomToFit(600, FIT_PADDING, (n) => hitIds.has(String(n.id)));
    else fg.zoomToFit(600, FIT_PADDING);
  };

  // A picked node that a filter took off the map is no longer picked.
  useEffect(() => {
    if (picked && !filtered.nodes.some((n) => n.id === picked.id)) setPicked(null);
  }, [filtered, picked]);

  // Search focuses the map on what it found.
  useEffect(() => {
    if (searching && hitIds.size) frame(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hitIds]);

  const toggle = (group: OrbGroup) =>
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(group)) next.delete(group);
      else next.add(group);
      return next;
    });

  const focusNode = (node: OrbNode) => {
    setPicked(node);
    const drawn = shown.nodes.find((n) => n.id === node.id);
    if (drawn?.x !== undefined) {
      graphRef.current?.centerAt(drawn.x, drawn.y, 600);
      graphRef.current?.zoom(3, 600);
    }
  };

  const open = (node: OrbNode) => {
    if (node.slug) requestWikiPage(node.slug);
    else if (node.section) setActiveSection(node.section);
  };
  const focusNodeRef = useRef(focusNode);
  focusNodeRef.current = focusNode;
  const openRef = useRef(open);
  openRef.current = open;

  // Hands on the memory map: the same hover, click and open as the mouse.
  const latest = useRef({ shown, width, height, picked });
  latest.current = { shown, width, height, picked };
  useEffect(() => {
    if (mode !== "memory") return;
    registerHands(
      graphHandController<DrawNode>({
        graph: () => graphRef.current as never,
        size: () => ({ width: latest.current.width, height: latest.current.height }),
        nodes: () => latest.current.shown.nodes,
        picked: () => latest.current.shown.nodes.find((n) => n.id === latest.current.picked?.id) ?? null,
        hover: (node) => setHover(node),
        pick: (node) => (node ? focusNodeRef.current(node) : setPicked(null)),
        open: (node) => openRef.current(node),
      }),
    );
    return () => registerHands(null);
  }, [mode, registerHands]);

  const submit = async () => {
    const text = ask.trim();
    if (!text) return;
    setSentAt(Date.now());
    if (await sendChatMessage(text)) setAsk("");
  };

  const drawNode = (node: DrawNode, ctx: CanvasRenderingContext2D, scale: number) => {
    const colour = ORB_GROUP_COLOUR[node.group];
    const isHit = hitIds.has(node.id);
    const focus = hover?.id === node.id || picked?.id === node.id;
    const r = focus || isHit ? NODE_RADIUS * 1.6 : NODE_RADIUS;
    const x = node.x ?? 0;
    const y = node.y ?? 0;
    // Dim means one thing only: not a search hit.
    ctx.globalAlpha = searching && hitIds.size && !isHit ? 0.12 : 1;
    ctx.beginPath();
    ctx.arc(x, y, r, 0, 2 * Math.PI);
    // Off (a disabled skill, a server that is off) is a hollow ring.
    if (node.live) {
      ctx.fillStyle = colour;
      ctx.fill();
    } else {
      ctx.lineWidth = 1.2 / Math.max(scale, 0.5);
      ctx.strokeStyle = colour;
      ctx.stroke();
    }
    const showLabel = focus || (isHit && hitIds.size <= 40) || scale > 2.4;
    if (showLabel) {
      const size = Math.max(10 / scale, 2.5);
      ctx.font = `${focus || isHit ? 600 : 400} ${size}px ui-sans-serif, system-ui`;
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      ctx.fillStyle = "rgba(225,235,255,0.85)";
      ctx.fillText(node.label, x, y + r + 2 / scale);
    }
    ctx.globalAlpha = 1;
  };

  // Each family's name, written over its region.
  const drawRegions = (ctx: CanvasRenderingContext2D, scale: number) => {
    const size = Math.max(14 / scale, 6);
    ctx.font = `700 ${size}px ui-sans-serif, system-ui`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    for (const group of presentGroups) {
      const centre = { x: 0, y: 0, n: 0 };
      for (const node of shown.nodes) {
        if (node.group !== group || node.x === undefined) continue;
        centre.x += node.x;
        centre.y += node.y ?? 0;
        centre.n += 1;
      }
      if (!centre.n) continue;
      let top = Infinity;
      for (const node of shown.nodes) if (node.group === group && node.y !== undefined) top = Math.min(top, node.y);
      ctx.globalAlpha = 0.75;
      ctx.fillStyle = ORB_GROUP_COLOUR[group];
      ctx.fillText(groupLabel(group), centre.x / centre.n, top - size * 1.2);
    }
    ctx.globalAlpha = 1;
  };

  const from = t(full.capabilitySource === "hermes" ? "orb2.memory.from_hermes" : "orb2.memory.from_jarvis");
  const headline =
    mode === "projects"
      ? t("orb2.headline_projects")
      : isLoading
        ? t("orb2.memory.mapping")
        : hidden.size
          ? fill(t("orb2.memory.shown_of"), { shown: filtered.nodes.length, total })
          : fill(t("orb2.memory.total"), { total });
  const model = brainModel && brainModel.toLowerCase() !== "unknown" ? brainModel : "";
  const brainName = brainProvider && brainProvider !== "unknown" ? brainProvider : "";

  return (
    <div
      data-testid="memory-orb-view"
      className="relative h-full w-full overflow-hidden bg-[#05070b]"
    >
      {/* HUD backdrop: a faint radial glow and two thin rings behind the map. */}
      <div aria-hidden className="pointer-events-none absolute inset-0">
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,rgba(79,209,232,0.07),transparent_60%)]" />
        <div className="absolute left-1/2 top-1/2 h-[70vmin] w-[70vmin] -translate-x-1/2 -translate-y-1/2 rounded-full border border-cyan-300/[0.06]" />
        <div className="absolute left-1/2 top-1/2 h-[46vmin] w-[46vmin] -translate-x-1/2 -translate-y-1/2 rounded-full border border-dashed border-amber-300/[0.05]" />
      </div>

      {mode === "projects" ? (
        <ProjectsOrb registerHands={registerHands} />
      ) : (
        <div ref={wrapRef} className="absolute inset-0">
          {width > 0 && (
            <ForceGraph2D
              ref={graphRef}
              graphData={shown}
              width={width}
              height={height}
              backgroundColor="rgba(0,0,0,0)"
              nodeId="id"
              nodeRelSize={NODE_RADIUS}
              nodeCanvasObject={drawNode}
              nodePointerAreaPaint={(node: DrawNode, colour, ctx) => {
                ctx.fillStyle = colour;
                ctx.beginPath();
                ctx.arc(node.x ?? 0, node.y ?? 0, 6, 0, 2 * Math.PI);
                ctx.fill();
              }}
              onRenderFramePost={drawRegions}
              linkColor={() => "rgba(91,212,164,0.35)"}
              linkWidth={0.8}
              cooldownTicks={200}
              onEngineStop={() => {
                if (framedFor.current === shownKey) return;
                framedFor.current = shownKey;
                frame(searching);
              }}
              onNodeHover={(node) => setHover((node as DrawNode | null) ?? null)}
              onNodeClick={(node) => focusNode(node as DrawNode)}
              onBackgroundClick={() => setPicked(null)}
            />
          )}
        </div>
      )}

      <HandOverlay tracking={tracking} />

      {/* Top bar: title, map switch, status; then the controls of the map shown. */}
      <div className="pointer-events-none absolute inset-x-0 top-0 z-20 flex items-start gap-3 p-4">
        <div className="pointer-events-auto">
          <div className="flex items-center gap-3">
            <h1 className="font-mono text-xs font-semibold uppercase tracking-[0.3em] text-cyan-200/90 [text-shadow:0_0_12px_rgba(79,209,232,0.55)]">
              {t("orb2.title")}
            </h1>
            <div
              role="tablist"
              aria-label={t("orb2.mode.label")}
              className="flex rounded-full border border-cyan-400/20 bg-black/50 p-0.5 backdrop-blur"
            >
              {(["projects", "memory"] as const).map((id) => {
                const Icon = id === "projects" ? Orbit : Network;
                return (
                  <button
                    key={id}
                    type="button"
                    role="tab"
                    aria-selected={mode === id}
                    data-testid={`orb2-mode-${id}`}
                    onClick={() => setMode(id)}
                    className={cn(
                      "flex items-center gap-1 rounded-full px-2.5 py-1 font-mono text-[10px] uppercase tracking-[0.18em] transition-colors",
                      mode === id ? "bg-cyan-400/15 text-cyan-100" : "text-slate-400 hover:text-slate-200",
                    )}
                  >
                    <Icon className="h-3 w-3" aria-hidden />
                    {t(`orb2.mode.${id}`)}
                  </button>
                );
              })}
            </div>
          </div>
          <p className="mt-1 text-xs text-slate-400" data-testid="memory-orb-headline">
            {headline}
          </p>
          <p className="mt-1 flex flex-wrap gap-x-3 font-mono text-[10px] uppercase tracking-[0.15em] text-slate-500">
            {!connected && <span className="text-amber-300">{t("orb2.not_connected")}</span>}
            {brainName && (
              <span title={t("orb2.brain_title")}>
                {t("orb2.brain")}: {brainName}
                {model && ` · ${model}`}
              </span>
            )}
            <span className={voiceReady ? "text-emerald-300/80" : undefined}>
              {t(voiceReady ? "orb2.voice_ready" : "orb2.voice_unavailable")}
            </span>
          </p>
          {mode === "memory" && (
            <>
              <div
                role="group"
                aria-label={t("orb2.memory.filter_label")}
                className="mt-2 flex max-w-[34rem] flex-wrap gap-1.5"
                title={fill(t("orb2.memory.filter_title"), { from })}
              >
                {ORB_GROUPS.map((group) => {
                  const missing = full.unavailable.includes(group);
                  return (
                    <button
                      key={group}
                      type="button"
                      aria-pressed={!hidden.has(group)}
                      disabled={missing}
                      title={missing ? t("orb2.memory.unreadable") : undefined}
                      onClick={() => toggle(group)}
                      className={cn(
                        "flex items-center gap-1.5 rounded-full border border-white/10 bg-black/50 px-2.5 py-1 text-[11px] backdrop-blur hover:border-white/25 disabled:cursor-not-allowed",
                        (hidden.has(group) || missing) && "opacity-40",
                      )}
                    >
                      <span className="h-2 w-2 rounded-full" style={{ background: ORB_GROUP_COLOUR[group] }} />
                      <span className="text-slate-200">{groupLabel(group)}</span>
                      <span className="font-mono text-slate-500">
                        {missing ? t("orb2.memory.unavailable") : full.counts[group]}
                      </span>
                    </button>
                  );
                })}
              </div>
              <p className="mt-1.5 text-[10px] text-slate-500">{fill(t("orb2.memory.legend"), { from })}</p>
            </>
          )}
        </div>
        {mode === "memory" && (
          <>
            <div className="pointer-events-auto ml-auto w-72">
              <label className="flex h-9 items-center gap-2 rounded-full border border-cyan-400/20 bg-black/50 px-3 text-slate-300 backdrop-blur focus-within:border-cyan-300/60">
                <Search className="h-3.5 w-3.5 shrink-0" aria-hidden />
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && hits[0]) focusNode(hits[0]);
                    if (e.key === "Escape") setQuery("");
                  }}
                  placeholder={t("orb2.memory.search_placeholder")}
                  aria-label={t("orb2.memory.search_label")}
                  className="min-w-0 flex-1 bg-transparent text-xs outline-none placeholder:text-slate-500"
                />
                {query && (
                  <button type="button" aria-label={t("orb2.rail.clear")} onClick={() => setQuery("")}>
                    <X className="h-3.5 w-3.5" />
                  </button>
                )}
              </label>
              {searching && (
                <div
                  data-testid="memory-orb-results"
                  className="mt-2 rounded-xl border border-white/10 bg-black/70 p-2 text-xs backdrop-blur"
                >
                  <p className="px-1.5 pb-1 text-slate-400">
                    {hits.length ? fill(t("orb2.memory.matches"), { count: hits.length }) : t("orb2.memory.no_matches")}
                  </p>
                  {hits.slice(0, MAX_LISTED_HITS).map((node) => (
                    <button
                      key={node.id}
                      type="button"
                      onClick={() => focusNode(node)}
                      className="flex w-full items-center gap-2 rounded-md px-1.5 py-1 text-left hover:bg-white/5"
                    >
                      <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: ORB_GROUP_COLOUR[node.group] }} />
                      <span className="truncate text-slate-200">{node.label}</span>
                      <span className="ml-auto shrink-0 text-slate-500">{groupLabel(node.group)}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
            <MemoryOrbImport defaultOpen={importOpen} />
            <button
              type="button"
              onClick={() => frame(searching)}
              className="pointer-events-auto flex h-9 items-center gap-1.5 rounded-full border border-cyan-400/20 bg-black/50 px-3 text-xs text-slate-200 backdrop-blur hover:border-cyan-300/60"
            >
              <Crosshair className="h-3.5 w-3.5" aria-hidden /> {t("orb2.memory.fit")}
            </button>
          </>
        )}
        <HandToggle tracking={tracking} className={mode === "memory" ? undefined : "ml-auto"} />
      </div>

      {/* Detail of the hovered or picked node. */}
      {mode === "memory" && (picked ?? hover) && (
        <div className="absolute bottom-24 left-4 w-72 rounded-xl border border-white/10 bg-black/65 p-3 text-xs backdrop-blur">
          {(() => {
            const node = (picked ?? hover) as OrbNode;
            return (
              <>
                <p className="flex items-center gap-2 font-semibold text-slate-100">
                  <span className="h-2 w-2 rounded-full" style={{ background: ORB_GROUP_COLOUR[node.group] }} />
                  {node.label}
                </p>
                <p className="mt-0.5 text-slate-500">{groupLabel(node.group)}</p>
                {node.detail && <p className="mt-1 leading-5 text-slate-400">{node.detail}</p>}
                {picked && (node.slug || node.section) && (
                  <button
                    type="button"
                    onClick={() => open(node)}
                    className="mt-2 rounded-full bg-cyan-400/15 px-3 py-1 text-cyan-200 hover:bg-cyan-400/25"
                  >
                    {node.slug ? t("orb2.memory.open_note") : fill(t("orb2.memory.open_group"), { group: groupLabel(node.group) })}
                  </button>
                )}
              </>
            );
          })()}
        </div>
      )}

      {/* Ask bar and the answer. */}
      <div className="pointer-events-none absolute inset-x-0 bottom-0 z-10 flex flex-col items-center gap-3 p-5">
        {(thinking && sentAt !== null && !reply) || reply ? (
          <div
            data-testid="memory-orb-reply"
            className="pointer-events-auto max-h-48 max-w-xl overflow-y-auto rounded-xl border border-cyan-400/20 bg-black/70 px-4 py-3 text-sm leading-6 text-slate-100 backdrop-blur"
          >
            {reply ? reply.content : <span className="text-slate-400">{t("orb2.thinking")}</span>}
          </div>
        ) : null}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void submit();
          }}
          className="pointer-events-auto flex w-full max-w-xl items-center gap-2 rounded-full border border-cyan-400/25 bg-black/60 py-1.5 pl-4 pr-1.5 backdrop-blur focus-within:border-cyan-300/70"
        >
          <input
            value={ask}
            onChange={(e) => setAsk(e.target.value)}
            placeholder={t("orb2.ask_placeholder")}
            aria-label={t("orb2.ask_label")}
            className="min-w-0 flex-1 bg-transparent text-sm text-slate-100 outline-none placeholder:text-slate-500"
          />
          <button
            type="submit"
            aria-label={t("orb2.send")}
            disabled={!ask.trim() || !connected}
            className="flex h-8 w-8 items-center justify-center rounded-full bg-cyan-400/90 text-black disabled:opacity-40"
          >
            <Send className="h-4 w-4" />
          </button>
        </form>
      </div>
    </div>
  );
}
