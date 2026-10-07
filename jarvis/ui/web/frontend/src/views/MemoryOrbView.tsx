// The memory orb: one living map of everything the assistant remembers and can
// do, with the reactor watching over it and an ask bar underneath.
//
// The wiki map in the Notes section draws memory alone. This view fetches the
// five catalogs the app already serves (wiki graph, skills, tools, apps, MCP
// servers), joins them through `lib/orbGraph.ts` and draws them on the same
// force-graph library the wiki uses. Typing in the ask bar sends a normal chat
// turn (`lib/chat.ts`), so the answer also lands in the chat history.
import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import ForceGraph2D from "react-force-graph-2d";
import type { ForceGraphMethods, NodeObject } from "react-force-graph-2d";
import { Crosshair, Search, Send, X } from "lucide-react";

import { ReactorCore } from "@/components/deck/ReactorCore";
import { sendChatMessage } from "@/lib/chat";
import {
  ORB_GROUP_COLOUR,
  ORB_GROUP_LABEL,
  ORB_GROUPS,
  buildOrbGraph,
  filterOrbGraph,
  matchOrbNodes,
  type OrbGroup,
  type OrbNode,
  type OrbSources,
} from "@/lib/orbGraph";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";

import { MemoryOrbImport } from "./MemoryOrbImport";

type DrawNode = NodeObject<OrbNode> & OrbNode;

/** Family hubs hang off the core; category hubs hang off a family hub. */
function isFamily(node: OrbNode): boolean {
  return node.id.startsWith("hub:");
}

function radius(node: OrbNode): number {
  if (node.group === "core") return 10;
  if (isFamily(node)) return 7;
  return node.hub ? 4 : 3;
}

async function getJson<T>(url: string): Promise<T | undefined> {
  try {
    const res = await fetch(url);
    return res.ok ? ((await res.json()) as T) : undefined;
  } catch {
    // One unreachable catalog must not blank the whole map: its family is
    // simply left out, which the legend shows as a zero.
    return undefined;
  }
}

async function fetchSources(): Promise<OrbSources> {
  const [wiki, skills, tools, apps, mcp] = await Promise.all([
    getJson<OrbSources["wiki"]>("/api/wiki/graph"),
    getJson<{ skills: OrbSources["skills"] }>("/api/skills"),
    getJson<{ tools: OrbSources["tools"] }>("/api/tools"),
    getJson<{ plugins: OrbSources["apps"] }>("/api/marketplace/plugins"),
    getJson<{ servers: OrbSources["mcp"] }>("/api/mcps"),
  ]);
  return {
    wiki,
    skills: skills?.skills,
    tools: tools?.tools,
    apps: apps?.plugins,
    mcp: mcp?.servers,
  };
}

function useSize(ref: React.RefObject<HTMLDivElement>) {
  const [size, setSize] = useState({ width: 800, height: 600 });
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      if (width > 0 && height > 0) setSize({ width, height });
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref]);
  return size;
}

export function MemoryOrbView() {
  const wrapRef = useRef<HTMLDivElement>(null);
  const graphRef = useRef<ForceGraphMethods<DrawNode> | undefined>(undefined);
  const { width, height } = useSize(wrapRef);
  const [hidden, setHidden] = useState<Set<OrbGroup>>(new Set());
  const [query, setQuery] = useState("");
  const [hover, setHover] = useState<OrbNode | null>(null);
  const [picked, setPicked] = useState<OrbNode | null>(null);
  const [ask, setAsk] = useState("");
  const [sentAt, setSentAt] = useState<number | null>(null);
  // Fit the camera to the map once, when the first layout has settled.
  const fitted = useRef(false);

  const voiceState = useEventStore((s) => s.voiceState);
  const connected = useEventStore((s) => s.connected);
  const thinking = useEventStore((s) => s.chatThinking);
  const messages = useEventStore((s) => s.messages);
  const setActiveSection = useEventStore((s) => s.setActiveSection);

  const { data: sources, isLoading } = useQuery({
    queryKey: ["orb", "sources"],
    queryFn: fetchSources,
    staleTime: 30_000,
  });
  const { data: brain } = useQuery({
    queryKey: ["brain", "status"],
    queryFn: () => getJson<{ provider?: string; model?: string }>("/api/brain/status"),
    staleTime: 15_000,
  });

  const full = useMemo(() => buildOrbGraph(sources ?? {}), [sources]);
  // The force engine mutates the objects it is given, so it gets fresh copies
  // whenever the filter changes and keeps them otherwise.
  const shown = useMemo(() => {
    const g = filterOrbGraph(full, hidden);
    return {
      nodes: g.nodes.map((n) => ({ ...n })) as DrawNode[],
      links: g.links.map((l) => ({ ...l })),
    };
  }, [full, hidden]);
  const matches = useMemo(() => matchOrbNodes(full, query), [full, query]);

  // The answer to the last question asked here: the first assistant message
  // that arrived after it was sent.
  const reply = useMemo(() => {
    if (sentAt === null) return null;
    return [...messages].reverse().find((m) => m.role === "assistant" && m.ts >= sentAt - 1000) ?? null;
  }, [messages, sentAt]);

  useEffect(() => {
    const fg = graphRef.current;
    if (!fg) return;
    fg.d3Force("charge")?.strength(-60);
    fg.d3Force("link")?.distance((l: { target: DrawNode }) =>
      isFamily(l.target) ? 95 : l.target.hub ? 45 : 16,
    );
  }, [shown]);

  const toggle = (group: OrbGroup) =>
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(group)) next.delete(group);
      else next.add(group);
      return next;
    });

  const submit = async () => {
    const text = ask.trim();
    if (!text) return;
    setSentAt(Date.now());
    if (await sendChatMessage(text)) setAsk("");
  };

  const drawNode = (node: DrawNode, ctx: CanvasRenderingContext2D, scale: number) => {
    const colour = ORB_GROUP_COLOUR[node.group];
    const r = radius(node);
    const lit = matches.size === 0 || matches.has(node.id);
    const focus = hover?.id === node.id || picked?.id === node.id;
    ctx.globalAlpha = lit ? (node.live ? 1 : 0.35) : 0.12;
    ctx.shadowColor = colour;
    ctx.shadowBlur = node.live && lit ? (node.hub ? 20 : 10) : 0;
    ctx.beginPath();
    ctx.arc(node.x ?? 0, node.y ?? 0, focus ? r * 1.6 : r, 0, 2 * Math.PI);
    ctx.fillStyle = colour;
    ctx.fill();
    ctx.shadowBlur = 0;
    const family = node.group === "core" || isFamily(node);
    const showLabel =
      family ||
      focus ||
      (node.hub && scale > 1.3) ||
      (matches.has(node.id) && matches.size < 40) ||
      scale > 2.6;
    if (showLabel) {
      const size = (family ? 13 : node.hub ? 10 : 8.5) / Math.max(scale, 0.6);
      ctx.font = `${family ? 700 : node.hub ? 600 : 400} ${size}px ui-sans-serif, system-ui`;
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      ctx.fillStyle = family ? "#f5f7ff" : node.hub ? "rgba(225,235,255,0.85)" : "rgba(225,235,255,0.7)";
      ctx.fillText(node.label, node.x ?? 0, (node.y ?? 0) + r + 2 / scale);
    }
    ctx.globalAlpha = 1;
  };

  const online = connected && Boolean(brain?.provider);

  return (
    <div
      data-testid="memory-orb-view"
      className="relative h-full w-full overflow-hidden bg-[radial-gradient(ellipse_at_center,#0b1622_0%,#05070b_70%)]"
    >
      <div ref={wrapRef} className="absolute inset-0">
        <ForceGraph2D
          ref={graphRef}
          graphData={shown}
          width={width}
          height={height}
          backgroundColor="rgba(0,0,0,0)"
          nodeId="id"
          nodeRelSize={4}
          nodeCanvasObject={drawNode}
          nodePointerAreaPaint={(node: DrawNode, colour, ctx) => {
            ctx.fillStyle = colour;
            ctx.beginPath();
            ctx.arc(node.x ?? 0, node.y ?? 0, node.hub ? 8 : 5, 0, 2 * Math.PI);
            ctx.fill();
          }}
          linkColor={() => "rgba(120,200,255,0.12)"}
          linkWidth={0.6}
          cooldownTicks={220}
          onEngineStop={() => {
            if (fitted.current) return;
            fitted.current = true;
            graphRef.current?.zoomToFit(600, 80);
          }}
          onNodeHover={(node) => setHover((node as DrawNode | null) ?? null)}
          onNodeClick={(node) => {
            setPicked(node as DrawNode);
            graphRef.current?.centerAt((node as DrawNode).x, (node as DrawNode).y, 600);
            graphRef.current?.zoom(3, 600);
          }}
          onBackgroundClick={() => setPicked(null)}
        />
      </div>

      {/* Top bar: search and fit. */}
      <div className="pointer-events-none absolute inset-x-0 top-0 flex items-start gap-3 p-4">
        <div className="pointer-events-auto">
          <h1 className="font-mono text-xs font-semibold uppercase tracking-[0.3em] text-cyan-200/90">
            Memory orb
          </h1>
          <p className="mt-1 text-xs text-slate-400">
            {isLoading ? "Mapping…" : `${full.nodes.length - 1} things the assistant knows or can use`}
          </p>
        </div>
        <label className="pointer-events-auto ml-auto flex h-9 w-72 items-center gap-2 rounded-full border border-cyan-400/20 bg-black/50 px-3 text-slate-300 backdrop-blur focus-within:border-cyan-300/60">
          <Search className="h-3.5 w-3.5 shrink-0" aria-hidden />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
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
        <MemoryOrbImport />
        <button
          type="button"
          onClick={() => graphRef.current?.zoomToFit(600, 60)}
          className="pointer-events-auto flex h-9 items-center gap-1.5 rounded-full border border-cyan-400/20 bg-black/50 px-3 text-xs text-slate-200 backdrop-blur hover:border-cyan-300/60"
        >
          <Crosshair className="h-3.5 w-3.5" aria-hidden /> Fit
        </button>
      </div>

      {/* Legend and filters. */}
      <div
        role="group"
        aria-label="Filter the map"
        className="absolute right-4 top-16 w-48 rounded-xl border border-white/10 bg-black/55 p-3 backdrop-blur"
      >
        <p className="mb-2 font-mono text-[10px] uppercase tracking-[0.25em] text-slate-500">Filter</p>
        {ORB_GROUPS.map((group) => (
          <button
            key={group}
            type="button"
            aria-pressed={!hidden.has(group)}
            onClick={() => toggle(group)}
            className={cn(
              "flex w-full items-center gap-2 rounded-md px-1.5 py-1 text-xs transition-opacity hover:bg-white/5",
              hidden.has(group) && "opacity-35",
            )}
          >
            <span
              className="h-2.5 w-2.5 rounded-full"
              style={{ background: ORB_GROUP_COLOUR[group], boxShadow: `0 0 8px ${ORB_GROUP_COLOUR[group]}` }}
            />
            <span className="text-slate-200">{ORB_GROUP_LABEL[group]}</span>
            <span className="ml-auto font-mono text-slate-500">{full.counts[group]}</span>
          </button>
        ))}
      </div>

      {/* The reactor and the brain it is running on. */}
      <div className="pointer-events-none absolute bottom-28 right-6 flex flex-col items-center gap-2">
        <div className="rounded-full shadow-[0_0_60px_rgba(79,209,232,0.35)]">
          <ReactorCore size={150} voiceState={voiceState} />
        </div>
        <div className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.2em]">
          <span className={cn("flex items-center gap-1.5", online ? "text-emerald-300" : "text-slate-500")}>
            <span className={cn("h-1.5 w-1.5 rounded-full", online ? "bg-emerald-400" : "bg-slate-500")} />
            {online ? "Online" : "Offline"}
          </span>
          {brain?.model && (
            <span className="max-w-[160px] truncate rounded-full border border-cyan-400/30 px-2 py-0.5 text-cyan-200">
              {brain.model}
            </span>
          )}
        </div>
      </div>

      {/* Detail of the hovered or picked node. */}
      {(picked ?? hover) && (
        <div className="absolute left-4 top-20 w-72 rounded-xl border border-white/10 bg-black/65 p-3 text-xs backdrop-blur">
          {(() => {
            const node = (picked ?? hover) as OrbNode;
            return (
              <>
                <p className="flex items-center gap-2 font-semibold text-slate-100">
                  <span className="h-2 w-2 rounded-full" style={{ background: ORB_GROUP_COLOUR[node.group] }} />
                  {node.label}
                </p>
                {node.detail && <p className="mt-1 leading-5 text-slate-400">{node.detail}</p>}
                {picked && node.section && (
                  <button
                    type="button"
                    onClick={() => setActiveSection(node.section!)}
                    className="mt-2 rounded-full bg-cyan-400/15 px-3 py-1 text-cyan-200 hover:bg-cyan-400/25"
                  >
                    Open
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
            className="max-w-xl rounded-xl border border-cyan-400/20 bg-black/70 px-4 py-3 text-sm leading-6 text-slate-100 backdrop-blur"
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
