// The memory orb: one map of what the assistant remembers and what it can use,
// with an ask bar underneath.
//
// Only real things and real links are drawn (`lib/orbGraph.ts`): notes linked
// as the vault links them, and each family (notes, skills, tools, connected
// apps, MCP servers) as a labelled region. Capabilities come from Hermes Agent
// when it is reachable, the same list the Tool Armory shows. A catalog that
// could not be read is marked unavailable, never shown as zero. Typing in the
// ask bar sends a normal chat turn (`lib/chat.ts`).
import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import ForceGraph2D from "react-force-graph-2d";
import type { ForceGraphMethods, NodeObject } from "react-force-graph-2d";
import { Crosshair, Search, Send, X } from "lucide-react";

import { sendChatMessage } from "@/lib/chat";
import {
  ORB_GROUP_COLOUR,
  ORB_GROUP_LABEL,
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

function useSize(ref: React.RefObject<HTMLDivElement>) {
  const [size, setSize] = useState({ width: 0, height: 0 });
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    setSize({ width: el.clientWidth, height: el.clientHeight });
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      if (width > 0 && height > 0) setSize({ width, height });
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref]);
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

export function MemoryOrbView() {
  const wrapRef = useRef<HTMLDivElement>(null);
  const graphRef = useRef<ForceGraphMethods<DrawNode> | undefined>(undefined);
  const { width, height } = useSize(wrapRef);
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
      ctx.fillText(ORB_GROUP_LABEL[group], centre.x / centre.n, top - size * 1.2);
    }
    ctx.globalAlpha = 1;
  };

  const headline = isLoading
    ? "Mapping…"
    : hidden.size
      ? `${filtered.nodes.length} of ${total} shown`
      : `${total} things the assistant remembers or can use`;
  const model = brainModel && brainModel.toLowerCase() !== "unknown" ? brainModel : "";
  const brainName = brainProvider && brainProvider !== "unknown" ? brainProvider : "";

  return (
    <div
      data-testid="memory-orb-view"
      className="relative h-full w-full overflow-hidden bg-[#05070b]"
    >
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

      {/* Top bar: title, status, search, import and fit. */}
      <div className="pointer-events-none absolute inset-x-0 top-0 flex items-start gap-3 p-4">
        <div className="pointer-events-auto">
          <h1 className="font-mono text-xs font-semibold uppercase tracking-[0.3em] text-cyan-200/90">
            Memory orb
          </h1>
          <p className="mt-1 text-xs text-slate-400" data-testid="memory-orb-headline">
            {headline}
          </p>
          <p className="mt-1 flex flex-wrap gap-x-3 font-mono text-[10px] uppercase tracking-[0.15em] text-slate-500">
            {!connected && <span className="text-amber-300">Not connected to Jarvis</span>}
            {brainName && <span title="The brain answering turns">Brain: {brainName}{model && ` · ${model}`}</span>}
            <span className={voiceReady ? "text-emerald-300/80" : undefined}>
              {voiceReady ? "Voice ready" : "Voice unavailable"}
            </span>
          </p>
          <div
            role="group"
            aria-label="Filter the map"
            className="mt-2 flex max-w-[34rem] flex-wrap gap-1.5"
            title={`Skills, tools and servers from ${full.capabilitySource === "hermes" ? "Hermes Agent" : "Jarvis"}. Lines are links between notes. A ring is something switched off.`}
          >
            {ORB_GROUPS.map((group) => {
              const missing = full.unavailable.includes(group);
              return (
                <button
                  key={group}
                  type="button"
                  aria-pressed={!hidden.has(group)}
                  disabled={missing}
                  title={missing ? "Couldn't be read" : undefined}
                  onClick={() => toggle(group)}
                  className={cn(
                    "flex items-center gap-1.5 rounded-full border border-white/10 bg-black/50 px-2.5 py-1 text-[11px] backdrop-blur hover:border-white/25 disabled:cursor-not-allowed",
                    (hidden.has(group) || missing) && "opacity-40",
                  )}
                >
                  <span className="h-2 w-2 rounded-full" style={{ background: ORB_GROUP_COLOUR[group] }} />
                  <span className="text-slate-200">{ORB_GROUP_LABEL[group]}</span>
                  <span className="font-mono text-slate-500">{missing ? "unavailable" : full.counts[group]}</span>
                </button>
              );
            })}
          </div>
          <p className="mt-1.5 text-[10px] text-slate-500">
            From {full.capabilitySource === "hermes" ? "Hermes Agent" : "Jarvis"} · lines link notes · a ring is switched off
          </p>
        </div>
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
              placeholder="Find a note, skill, tool or app"
              aria-label="Search the memory orb"
              className="min-w-0 flex-1 bg-transparent text-xs outline-none placeholder:text-slate-500"
            />
            {query && (
              <button type="button" aria-label="Clear search" onClick={() => setQuery("")}>
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
                {hits.length ? `${hits.length} match${hits.length === 1 ? "" : "es"}` : "No matches on the map"}
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
                  <span className="ml-auto shrink-0 text-slate-500">{ORB_GROUP_LABEL[node.group]}</span>
                </button>
              ))}
            </div>
          )}
        </div>
        <MemoryOrbImport />
        <button
          type="button"
          onClick={() => frame(searching)}
          className="pointer-events-auto flex h-9 items-center gap-1.5 rounded-full border border-cyan-400/20 bg-black/50 px-3 text-xs text-slate-200 backdrop-blur hover:border-cyan-300/60"
        >
          <Crosshair className="h-3.5 w-3.5" aria-hidden /> Fit
        </button>
      </div>

      {/* Detail of the hovered or picked node. */}
      {(picked ?? hover) && (
        <div className="absolute bottom-24 left-4 w-72 rounded-xl border border-white/10 bg-black/65 p-3 text-xs backdrop-blur">
          {(() => {
            const node = (picked ?? hover) as OrbNode;
            return (
              <>
                <p className="flex items-center gap-2 font-semibold text-slate-100">
                  <span className="h-2 w-2 rounded-full" style={{ background: ORB_GROUP_COLOUR[node.group] }} />
                  {node.label}
                </p>
                <p className="mt-0.5 text-slate-500">{ORB_GROUP_LABEL[node.group]}</p>
                {node.detail && <p className="mt-1 leading-5 text-slate-400">{node.detail}</p>}
                {picked && (node.slug || node.section) && (
                  <button
                    type="button"
                    onClick={() => open(node)}
                    className="mt-2 rounded-full bg-cyan-400/15 px-3 py-1 text-cyan-200 hover:bg-cyan-400/25"
                  >
                    {node.slug ? "Open note" : `Open ${ORB_GROUP_LABEL[node.group]}`}
                  </button>
                )}
              </>
            );
          })()}
        </div>
      )}

      {/* Ask bar and the answer. */}
      <div className="absolute inset-x-0 bottom-0 flex flex-col items-center gap-3 p-5">
        {(thinking && sentAt !== null && !reply) || reply ? (
          <div
            data-testid="memory-orb-reply"
            className="max-h-48 max-w-xl overflow-y-auto rounded-xl border border-cyan-400/20 bg-black/70 px-4 py-3 text-sm leading-6 text-slate-100 backdrop-blur"
          >
            {reply ? reply.content : <span className="text-slate-400">Thinking…</span>}
          </div>
        ) : null}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void submit();
          }}
          className="flex w-full max-w-xl items-center gap-2 rounded-full border border-cyan-400/25 bg-black/60 py-1.5 pl-4 pr-1.5 backdrop-blur focus-within:border-cyan-300/70"
        >
          <input
            value={ask}
            onChange={(e) => setAsk(e.target.value)}
            placeholder='Ask anything, e.g. "what do you know about me?"'
            aria-label="Ask the assistant"
            className="min-w-0 flex-1 bg-transparent text-sm text-slate-100 outline-none placeholder:text-slate-500"
          />
          <button
            type="submit"
            aria-label="Send"
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
