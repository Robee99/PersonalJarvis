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
// apps, MCP servers) as a labelled region. Inside a family, memories branch
// off by section (`lib/orbSections.ts`): a vault area, or a link or topic
// community of a large one. Families and sections are neutral; each memory
// has its own colour, its section's hue shifted by its own id. The section
// bar and the [ and ] keys jump from section to section and frame it.
// Capabilities come from Hermes Agent
// when it is reachable, the same list the Tool Armory shows. A catalog that
// could not be read is marked unavailable, never shown as zero. Typing in the
// ask bar sends a normal chat turn (`lib/chat.ts`).
import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import ForceGraph2D from "react-force-graph-2d";
import type { ForceGraphMethods, NodeObject } from "react-force-graph-2d";
import { Box, ChevronLeft, ChevronRight, Crosshair, Keyboard, ListTree, Network, Orbit, Search, Send, Square, X } from "lucide-react";

import { HandOverlay, HandToggle } from "@/components/orb/hands/HandControls";
import { GESTURES, type GestureId } from "@/components/orb/hands/gestures";
import { graphHandController, handMapController, sceneBridge } from "@/components/orb/hands/graphController";
import { useHandTracking, type HandController, type HandDeps } from "@/components/orb/hands/useHandTracking";
import type { MemoryGraph3DHandle } from "@/components/orb/MemoryGraph3D";
import { ProjectsOrb } from "@/components/orb/ProjectsOrb";
import { fill, useT } from "@/i18n";
import { useConversations } from "@/hooks/useConversations";
import { sendChatMessage } from "@/lib/chat";
import { useWebglSupported } from "@/lib/graphDimension";
import {
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
  type OrbLink,
  type OrbNode,
  type OrbNoteGroups,
  type OrbSources,
} from "@/lib/orbGraph";
import {
  SECTION_LABEL,
  SECTION_NEUTRAL,
  buildSections,
  sectionFrameIds,
  stepSection,
  type OrbSection,
} from "@/lib/orbSections";
import { isTextEntryTarget } from "@/lib/shortcutOverlayTrigger";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";
import { useRailStore } from "@/store/rail";

import { MemoryOrbImport } from "./MemoryOrbImport";

/** A memory, or a section's branch point (a label on the map, never a memory). */
type FlatNode = OrbNode & { branch?: boolean };
type DrawNode = NodeObject<FlatNode> & FlatNode;
type FlatLink = OrbLink & { kind?: "section" };

const NODE_RADIUS = 3.2;
const REGION_RADIUS = 260;
const MAX_LISTED_HITS = 8;
/** Room around a framed map for the title, the legend and the ask bar. */
const FIT_PADDING = 140;
const MODE_KEY = "orb2.mode";
const DIMENSION_KEY = "orb2.dimension";

// The WebGL scene is the largest dependency in the app; it is only fetched
// when the memory map is shown in 3D.
const MemoryGraph3D = lazy(() => import("@/components/orb/MemoryGraph3D"));

export type OrbMode = "projects" | "memory";
export type OrbDimension = "2d" | "3d";

/** The memory map opens in 3D unless the person chose the flat map. */
function savedDimension(): OrbDimension {
  try {
    return window.localStorage.getItem(DIMENSION_KEY) === "2d" ? "2d" : "3d";
  } catch {
    // Storage can be blocked; 3D is the default.
    return "3d";
  }
}

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

async function postJson<T>(url: string, body: unknown): Promise<T | undefined> {
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return res.ok ? ((await res.json()) as T) : undefined;
  } catch {
    return undefined;
  }
}

type OrbConversation = NonNullable<OrbSources["conversations"]>[number];

/** Cognee sources, the conversations they index, and GraphRAG communities. */
async function fetchSemanticSources(): Promise<Pick<OrbSources, "semanticIndex" | "conversations" | "graphrag">> {
  const [semanticIndex, conversations, graphrag] = await Promise.all([
    getJson<OrbSources["semanticIndex"]>("/api/wiki/semantic-index"),
    getJson<OrbConversation[]>("/api/chats?limit=1000"),
    getJson<OrbSources["graphrag"]>("/api/wiki/graphrag-communities"),
  ]);
  const indexedIds = (semanticIndex?.conversation_sources ?? [])
    .map((source) => source.thread_id)
    .filter(Boolean)
    .slice(0, 1000);
  const indexed = indexedIds.length
    ? await postJson<OrbConversation[]>("/api/chats/indexed-summaries", { ids: indexedIds })
    : [];
  const byKey = new Map<string, OrbConversation>();
  for (const item of [...(indexed ?? []), ...(Array.isArray(conversations) ? conversations : [])]) {
    byKey.set(`${item.kind}:${item.id}`, item);
  }
  return {
    semanticIndex: semanticIndex?.enabled ? semanticIndex : undefined,
    conversations: [...byKey.values()],
    graphrag,
  };
}

export async function fetchOrbSources(): Promise<OrbSources> {
  const [wiki, hermes, apps, semantic, tree] = await Promise.all([
    getJson<OrbSources["wiki"]>("/api/wiki/graph"),
    getJson<HermesCatalogs>("/api/hermes/inventory"),
    getJson<{ plugins: MarketplaceApp[] }>("/api/marketplace/plugins"),
    fetchSemanticSources(),
    // The note communities that sections follow; without them, vault folders.
    getJson<Partial<OrbNoteGroups>>("/api/wiki/orb-tree"),
  ]);
  const base = {
    wiki: Array.isArray(wiki?.nodes) ? wiki : undefined,
    apps: connectedApps(apps?.plugins),
    noteGroups:
      Array.isArray(tree?.communities) && Array.isArray(tree?.leaves)
        ? { communities: tree.communities, leaves: tree.leaves }
        : undefined,
    ...semantic,
  };
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
  // HOLO on the rail starts and stops the hands; the map's own toggle still
  // works on its own. Closing the map turns HOLO off (StarkShell).
  const holo = useRailStore((s) => s.holo);
  const trackingRef = useRef(tracking);
  trackingRef.current = tracking;
  useEffect(() => {
    // HOLO is the ask: the tracking files are fetched if they are missing.
    if (holo) trackingRef.current.start({ install: true });
    else if (trackingRef.current.status !== "off") trackingRef.current.stop();
  }, [holo]);
  const wrapRef = useRef<HTMLDivElement>(null);
  const graphRef = useRef<ForceGraphMethods<DrawNode> | undefined>(undefined);
  const graph3dRef = useRef<MemoryGraph3DHandle | null>(null);
  const [dimension, setDimensionState] = useState<OrbDimension>(savedDimension);
  const setDimension = (next: OrbDimension) => {
    setDimensionState(next);
    try {
      window.localStorage.setItem(DIMENSION_KEY, next);
    } catch {
      // Remembering the view is a convenience; without storage it resets.
    }
  };
  // No WebGL (a VM, a locked-down WebView, a lost context): the flat map.
  const webgl = useWebglSupported();
  const in3d = dimension === "3d" && webgl;
  const { width, height } = useSize(wrapRef, mode === "memory");
  const groupLabel = (group: OrbGroup) => t(`orb2.memory.group.${group}`);
  const [hidden, setHidden] = useState<Set<OrbGroup>>(new Set());
  const [query, setQuery] = useState("");
  const settledQuery = useDebounced(query, 180);
  const [hover, setHover] = useState<OrbNode | null>(null);
  const [picked, setPicked] = useState<OrbNode | null>(null);
  const [ask, setAsk] = useState("");
  // The ask bar stays hidden until the keyboard button opens it: Jarvis is
  // spoken to here, and the bar covered the map (Roberto 2026-10-26).
  const [typing, setTyping] = useState(false);
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
  const { openConversation } = useConversations();

  const { data: sources, isLoading } = useQuery({
    queryKey: ["orb", "sources"],
    queryFn: fetchOrbSources,
    staleTime: 30_000,
    enabled: mode === "memory",
  });

  const full = useMemo(() => buildOrbGraph(sources ?? {}), [sources]);
  const filtered = useMemo(() => filterOrbGraph(full, hidden), [full, hidden]);
  const sections = useMemo(() => buildSections(filtered.nodes, sources?.noteGroups), [filtered, sources]);
  const [activeSectionId, setActiveSectionId] = useState<string | null>(null);
  const [sectionListOpen, setSectionListOpen] = useState(false);
  const activeSection = activeSectionId ? (sections.byId.get(activeSectionId) ?? null) : null;
  const sectionTitle = (section: OrbSection) => section.title ?? groupLabel(section.group);
  const colourOf = (node: OrbNode) => sections.colourOf.get(node.id) ?? SECTION_NEUTRAL;
  // The force engine mutates the objects it is given, so it gets fresh copies
  // whenever what is shown changes and keeps them otherwise. A family with
  // several sections gets a branch point per section, its members tied to it.
  const shown = useMemo(() => {
    const nodes = filtered.nodes.map((n) => ({ ...n })) as DrawNode[];
    const links: FlatLink[] = filtered.links.map((l) => ({ ...l }));
    for (const section of sections.list) {
      if (!section.branch) continue;
      nodes.push({ id: section.id, label: section.title ?? "", group: section.group, live: true, detail: "", branch: true });
      for (const id of section.memberIds) links.push({ source: id, target: section.id, kind: "section" });
    }
    return { nodes, links };
  }, [filtered, sections]);
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
    if (in3d || !fg) return;
    fg.d3Force("charge")?.strength(-28);
    // Members sit close round their section's branch point; wikilinks reach
    // further, and one into another section pulls only lightly, so each
    // section stays one cluster.
    const end = (e: unknown) => String(typeof e === "object" && e !== null ? (e as { id?: unknown }).id : e);
    const sectionOf = (e: unknown) => sections.ofNode.get(end(e))?.id;
    fg.d3Force("link")
      ?.distance((l: FlatLink) => (l.kind === "section" ? 14 : 28))
      .strength((l: FlatLink) =>
        l.kind === "section" ? 0.6 : sectionOf(l.source) === sectionOf(l.target) ? 0.4 : 0.04,
      );
    fg.d3Force("region", createRegionForce(anchors, 0.06) as never);
    fg.d3ReheatSimulation();
  }, [shown, anchors, in3d, sections]);

  const frame = (onlyHits: boolean) => {
    if (in3d) {
      graph3dRef.current?.fit(onlyHits && hitIds.size ? hitIds : undefined);
      return;
    }
    const fg = graphRef.current;
    if (!fg || !shown.nodes.length) return;
    if (onlyHits && hitIds.size) fg.zoomToFit(600, FIT_PADDING, (n) => hitIds.has(String(n.id)));
    else fg.zoomToFit(600, FIT_PADDING);
  };

  // A picked node that a filter took off the map is no longer picked.
  useEffect(() => {
    if (picked && !filtered.nodes.some((n) => n.id === picked.id)) setPicked(null);
  }, [filtered, picked]);
  // Nor is a section whose family was filtered off.
  useEffect(() => {
    if (activeSectionId && !sections.byId.has(activeSectionId)) setActiveSectionId(null);
  }, [sections, activeSectionId]);

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

  /** Frame a section on the map and make it the one the steppers move from. */
  // The section jumped to, ahead of the render that shows it, so presses of
  // [ or ] that land before that render still step one section each.
  const activeSectionRef = useRef(activeSectionId);
  activeSectionRef.current = activeSectionId;
  const jumpToSection = (section: OrbSection) => {
    activeSectionRef.current = section.id;
    setActiveSectionId(section.id);
    setSectionListOpen(false);
    const ids = sectionFrameIds(section);
    if (in3d) {
      graph3dRef.current?.fit(ids);
      return;
    }
    graphRef.current?.zoomToFit(600, FIT_PADDING, (n) => ids.has(String(n.id)));
  };
  /** The next or previous section from the one jumped to last, or from the picked memory's. */
  const stepSections = (delta: number) => {
    const from = activeSectionRef.current ?? (picked ? (sections.ofNode.get(picked.id)?.id ?? null) : null);
    const next = stepSection(sections.list, from, delta);
    if (next) jumpToSection(next);
  };
  const stepSectionsRef = useRef(stepSections);
  stepSectionsRef.current = stepSections;
  // [ and ] step through the sections wherever the focus is, except in a text field.
  useEffect(() => {
    if (mode !== "memory") return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "[" && e.key !== "]") return;
      // Ctrl+Alt is AltGr, which types the brackets on many layouts.
      if (e.metaKey || (e.ctrlKey && !e.altKey) || isTextEntryTarget(e.target)) return;
      e.preventDefault();
      stepSectionsRef.current(e.key === "]" ? 1 : -1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mode]);

  const focusNode = (node: OrbNode) => {
    setPicked(node);
    if (in3d) {
      graph3dRef.current?.focus(node.id);
      return;
    }
    const drawn = shown.nodes.find((n) => n.id === node.id);
    if (drawn?.x !== undefined) {
      graphRef.current?.centerAt(drawn.x, drawn.y, 600);
      graphRef.current?.zoom(3, 600);
    }
  };

  const open = (node: OrbNode) => {
    if (node.slug) requestWikiPage(node.slug);
    else if (node.group === "conversations" && node.threadId && node.conversationKind) {
      setActiveSection("chats");
      void openConversation(node.conversationKind, node.threadId);
    } else if (node.section) setActiveSection(node.section);
  };
  const openRef = useRef(open);
  openRef.current = open;

  // Hands on the memory map: the same hover, pick and open as the mouse, the
  // section steppers, and a reset that frames the whole map again. A hand's
  // pick only marks the memory (no fly-in), so it stays under the hand for
  // the pinch that opens it.
  const frameRef = useRef(frame);
  frameRef.current = frame;
  const latest = useRef({ shown, width, height, picked, hover });
  latest.current = { shown, width, height, picked, hover };
  useEffect(() => {
    if (mode !== "memory") return;
    const shared = {
      pick: (node: OrbNode | null) => setPicked(node),
      open: (node: OrbNode) => openRef.current(node),
      section: (step: 1 | -1) => stepSectionsRef.current(step),
      reset: () => {
        setActiveSectionId(null);
        frameRef.current(false);
      },
    };
    if (in3d) {
      // The 3D map hovers through its own picking: the hand moves its pointer
      // and zooms through its wheel, and reads back what it hovers.
      const scene = sceneBridge(
        () => wrapRef.current?.querySelector<HTMLCanvasElement>("[data-testid='memory-orb-3d'] canvas") ?? null,
      );
      registerHands(
        handMapController<OrbNode>({
          ...shared,
          nodeAt: (point) => {
            scene.move(point);
            return latest.current.hover;
          },
          picked: () => latest.current.picked,
          hover: (node) => setHover(node),
          zoom: (scale) => {
            scene.zoom(scale);
          },
          // Grabbing the cloud turns it, and so does twisting two pinches,
          // as in the Hands view; a pull flies in to the node.
          pan: (dx, dy) => graph3dRef.current?.orbit(dx, dy),
          rotate: (angle) => graph3dRef.current?.orbit(-angle / (2 * Math.PI), 0),
          pull: (node) => graph3dRef.current?.focus(node.id),
        }),
      );
    } else {
      registerHands(
        graphHandController<DrawNode>({
          ...shared,
          graph: () => graphRef.current as never,
          size: () => ({ width: latest.current.width, height: latest.current.height }),
          // Branch points are labels, not memories: hands pick memories only.
          nodes: () => latest.current.shown.nodes.filter((n) => !n.branch),
          picked: () => latest.current.shown.nodes.find((n) => n.id === latest.current.picked?.id) ?? null,
          hover: (node) => setHover(node),
        }),
      );
    }
    return () => registerHands(null);
  }, [mode, registerHands, in3d]);
  // What hands can do on the map shown: only the 3D map turns, the projects
  // map has no sections.
  const handGestures = useMemo<readonly GestureId[]>(
    () => GESTURES.filter((id) => (mode === "projects" ? id !== "swipe" && id !== "rotate" : in3d || id !== "rotate")),
    [mode, in3d],
  );

  const submit = async () => {
    const text = ask.trim();
    if (!text) return;
    setSentAt(Date.now());
    if (await sendChatMessage(text)) setAsk("");
  };

  // A section's branch point: a neutral ring with the section's name.
  const drawBranch = (node: DrawNode, ctx: CanvasRenderingContext2D, scale: number) => {
    const active = node.id === activeSectionId;
    const x = node.x ?? 0;
    const y = node.y ?? 0;
    const r = NODE_RADIUS * (active ? 2.2 : 1.7);
    ctx.globalAlpha = searching && hitIds.size ? 0.35 : 1;
    ctx.beginPath();
    ctx.arc(x, y, r, 0, 2 * Math.PI);
    ctx.fillStyle = "rgba(5,7,11,0.85)";
    ctx.fill();
    ctx.lineWidth = (active ? 2 : 1.3) / Math.max(scale, 0.5);
    ctx.strokeStyle = active ? "#ffffff" : SECTION_NEUTRAL;
    ctx.stroke();
    const size = Math.max(11 / scale, 3);
    ctx.font = `600 ${size}px ui-sans-serif, system-ui`;
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    ctx.fillStyle = active ? "#ffffff" : SECTION_LABEL;
    const name = node.label || groupLabel(node.group);
    ctx.fillText(name.length > 40 ? `${name.slice(0, 39)}…` : name, x, y + r + 2 / scale);
    ctx.globalAlpha = 1;
  };

  const drawNode = (node: DrawNode, ctx: CanvasRenderingContext2D, scale: number) => {
    if (node.branch) {
      drawBranch(node, ctx, scale);
      return;
    }
    const colour = colourOf(node);
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
    // A green ring: this note or conversation is in Cognee's source registry.
    if (node.semanticIndexed) {
      ctx.beginPath();
      ctx.arc(x, y, r + 2 / Math.max(scale, 0.6), 0, 2 * Math.PI);
      ctx.strokeStyle = "rgba(52,211,153,0.9)";
      ctx.lineWidth = 1.5 / Math.max(scale, 0.6);
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
      ctx.fillStyle = SECTION_LABEL;
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
      <div aria-hidden className={cn("pointer-events-none absolute inset-0", mode === "memory" && in3d && "hidden")}>
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,rgba(79,209,232,0.07),transparent_60%)]" />
        <div className="absolute left-1/2 top-1/2 h-[70vmin] w-[70vmin] -translate-x-1/2 -translate-y-1/2 rounded-full border border-cyan-300/[0.06]" />
        <div className="absolute left-1/2 top-1/2 h-[46vmin] w-[46vmin] -translate-x-1/2 -translate-y-1/2 rounded-full border border-dashed border-amber-300/[0.05]" />
      </div>

      {mode === "projects" ? (
        <ProjectsOrb registerHands={registerHands} />
      ) : (
        <div ref={wrapRef} className="absolute inset-0">
          {width > 0 && in3d && (
            <Suspense fallback={null}>
              <MemoryGraph3D
                ref={graph3dRef}
                nodes={filtered.nodes}
                links={filtered.links}
                sections={sections}
                activeSectionId={activeSectionId}
                onSection={setActiveSectionId}
                width={width}
                height={height}
                hitIds={hitIds}
                searching={searching}
                hoverId={hover?.id ?? null}
                pickedId={picked?.id ?? null}
                groupLabel={groupLabel}
                onHover={setHover}
                onPick={setPicked}
              />
            </Suspense>
          )}
          {width > 0 && !in3d && (
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
              linkColor={(l: FlatLink) => (l.kind === "section" ? "rgba(160,172,190,0.3)" : "rgba(91,212,164,0.35)")}
              linkWidth={0.8}
              cooldownTicks={200}
              onEngineStop={() => {
                if (framedFor.current === shownKey) return;
                framedFor.current = shownKey;
                frame(searching);
              }}
              onNodeHover={(node) => {
                const drawn = node as DrawNode | null;
                setHover(drawn && !drawn.branch ? drawn : null);
              }}
              onNodeClick={(node) => {
                const drawn = node as DrawNode;
                const section = drawn.branch ? sections.byId.get(drawn.id) : undefined;
                if (section) jumpToSection(section);
                else if (!drawn.branch) focusNode(drawn);
              }}
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
                      <span aria-hidden className="h-2 w-2 rounded-full border border-slate-400/70" />
                      <span className="text-slate-200">{groupLabel(group)}</span>
                      <span className="font-mono text-slate-500">
                        {missing ? t("orb2.memory.unavailable") : full.counts[group]}
                      </span>
                    </button>
                  );
                })}
              </div>
              <p className="mt-1.5 text-[10px] text-slate-500">{fill(t("orb2.memory.legend"), { from })}</p>
              <p className="mt-0.5 text-[10px] text-slate-500" data-testid="memory-orb-colour-legend">
                {t(in3d ? "orb2.memory.colour_legend_3d" : "orb2.memory.colour_legend")}
              </p>
              {sections.list.length > 0 && (
                <SectionBar
                  sections={sections.list}
                  active={activeSection}
                  open={sectionListOpen}
                  title={sectionTitle}
                  groupLabel={groupLabel}
                  onToggle={() => setSectionListOpen((v) => !v)}
                  onClose={() => setSectionListOpen(false)}
                  onStep={stepSections}
                  onJump={jumpToSection}
                />
              )}
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
                      <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: colourOf(node) }} />
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
            <div
              role="group"
              aria-label={t("orb2.memory.view_label")}
              className="pointer-events-auto flex h-9 items-center rounded-full border border-cyan-400/20 bg-black/50 p-0.5 backdrop-blur"
            >
              {(["3d", "2d"] as const).map((id) => {
                const Icon = id === "3d" ? Box : Square;
                const blocked = id === "3d" && !webgl;
                return (
                  <button
                    key={id}
                    type="button"
                    aria-pressed={(id === "3d") === in3d}
                    disabled={blocked}
                    title={blocked ? t("orb2.memory.view_no_webgl") : undefined}
                    data-testid={`memory-orb-view-${id}`}
                    onClick={() => setDimension(id)}
                    className={cn(
                      "flex h-full items-center gap-1 rounded-full px-2.5 font-mono text-[10px] uppercase tracking-[0.18em] transition-colors disabled:cursor-not-allowed disabled:opacity-40",
                      (id === "3d") === in3d ? "bg-cyan-400/15 text-cyan-100" : "text-slate-400 hover:text-slate-200",
                    )}
                  >
                    <Icon className="h-3 w-3" aria-hidden />
                    {t(`orb2.memory.view_${id}`)}
                  </button>
                );
              })}
            </div>
          </>
        )}
        <HandToggle tracking={tracking} gestures={handGestures} className={mode === "memory" ? undefined : "ml-auto"} />
      </div>

      {/* Detail of the hovered or picked node. */}
      {mode === "memory" && (picked ?? hover) && (
        <div className="absolute bottom-24 left-4 w-72 rounded-xl border border-white/10 bg-black/65 p-3 text-xs backdrop-blur">
          {(() => {
            const node = (picked ?? hover) as OrbNode;
            return (
              <>
                <p className="flex items-center gap-2 font-semibold text-slate-100">
                  <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: colourOf(node) }} />
                  {node.label}
                </p>
                <p className="mt-0.5 text-slate-500">
                  {groupLabel(node.group)}
                  {(() => {
                    const section = sections.ofNode.get(node.id);
                    return section?.title ? ` · ${section.title}` : "";
                  })()}
                </p>
                {node.detail && <p className="mt-1 leading-5 text-slate-400">{node.detail}</p>}
                {node.semanticIndexed && (
                  <p className="mt-2 font-mono text-[10px] uppercase tracking-wider text-emerald-300">
                    {fill(t("orb2.memory.cognee_indexed"), { source: node.sourceId?.slice(0, 12) ?? "" })}
                  </p>
                )}
                {node.group === "communities" && (
                  <div className="mt-3 border-t border-white/10 pt-2">
                    <p className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">
                      {t("orb2.memory.source_notes")}
                    </p>
                    {node.sourceLinks?.length ? (
                      <ul className="space-y-1">
                        {node.sourceLinks.map((source) => (
                          <li key={source.sourceId}>
                            {source.slug ? (
                              <button
                                type="button"
                                onClick={() => requestWikiPage(source.slug!)}
                                className="w-full truncate text-left text-cyan-200 hover:text-cyan-100"
                                title={source.sourcePath}
                              >
                                {source.sourcePath}
                              </button>
                            ) : (
                              <span className="block truncate text-slate-400" title={source.sourcePath}>
                                {source.sourcePath}
                              </span>
                            )}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="text-slate-500">{t("orb2.memory.no_source_links")}</p>
                    )}
                  </div>
                )}
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
        {!typing ? (
          <button
            type="button"
            aria-label={t("orb2.ask_label")}
            title={t("orb2.ask_label")}
            onClick={() => setTyping(true)}
            className="pointer-events-auto flex h-8 w-8 items-center justify-center rounded-full border border-cyan-400/20 bg-black/40 text-cyan-200/70 hover:text-cyan-100"
          >
            <Keyboard className="h-4 w-4" aria-hidden />
          </button>
        ) : (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void submit();
          }}
          onKeyDown={(e) => {
            if (e.key === "Escape") setTyping(false);
          }}
          className="pointer-events-auto flex w-full max-w-xl items-center gap-2 rounded-full border border-cyan-400/25 bg-black/60 py-1.5 pl-4 pr-1.5 backdrop-blur focus-within:border-cyan-300/70"
        >
          <input
            value={ask}
            onChange={(e) => setAsk(e.target.value)}
            placeholder={t("orb2.ask_placeholder")}
            autoFocus
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
        )}
      </div>
    </div>
  );
}

/**
 * The section bar: step to the previous or next section (also [ and ]), or
 * open the list of every section, family by family, and jump to one. A row's
 * swatch is the hue its memories' colours start from, so the list is the
 * legend of the map's colours as well.
 */
function SectionBar({
  sections,
  active,
  open,
  title,
  groupLabel,
  onToggle,
  onClose,
  onStep,
  onJump,
}: {
  sections: OrbSection[];
  active: OrbSection | null;
  open: boolean;
  title: (section: OrbSection) => string;
  groupLabel: (group: OrbGroup) => string;
  onToggle: () => void;
  onClose: () => void;
  onStep: (delta: number) => void;
  onJump: (section: OrbSection) => void;
}) {
  const t = useT();
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  const families = ORB_GROUPS.filter((g) => sections.some((s) => s.group === g));
  const stepClass =
    "flex h-full w-7 shrink-0 items-center justify-center rounded-full text-slate-400 hover:bg-white/5 hover:text-slate-100";
  return (
    <div className="relative mt-2 w-fit max-w-[34rem]" data-testid="memory-orb-sections">
      <div className="flex h-8 items-center rounded-full border border-white/10 bg-black/50 p-0.5 backdrop-blur">
        <button
          type="button"
          data-testid="memory-orb-section-prev"
          aria-label={t("orb2.memory.section_prev")}
          title={t("orb2.memory.section_prev")}
          onClick={() => onStep(-1)}
          className={stepClass}
        >
          <ChevronLeft className="h-3.5 w-3.5" aria-hidden />
        </button>
        <button
          type="button"
          data-testid="memory-orb-section-current"
          aria-expanded={open}
          aria-haspopup="listbox"
          title={t("orb2.memory.section_list")}
          onClick={onToggle}
          className="flex h-full min-w-0 items-center gap-1.5 rounded-full px-2 text-[11px] hover:bg-white/5"
        >
          <ListTree className="h-3.5 w-3.5 shrink-0 text-slate-400" aria-hidden />
          {active ? (
            <>
              <span aria-hidden className="h-2 w-2 shrink-0 rounded-full" style={{ background: active.colour }} />
              <span className="max-w-[14rem] truncate text-slate-100">{title(active)}</span>
              <span className="shrink-0 font-mono text-slate-500">
                {sections.indexOf(active) + 1}/{sections.length}
              </span>
            </>
          ) : (
            <span className="text-slate-300">{fill(t("orb2.memory.sections"), { count: sections.length })}</span>
          )}
        </button>
        <button
          type="button"
          data-testid="memory-orb-section-next"
          aria-label={t("orb2.memory.section_next")}
          title={t("orb2.memory.section_next")}
          onClick={() => onStep(1)}
          className={stepClass}
        >
          <ChevronRight className="h-3.5 w-3.5" aria-hidden />
        </button>
      </div>
      {open && (
        <div
          role="listbox"
          aria-label={t("orb2.memory.section_list")}
          data-testid="memory-orb-section-list"
          className="absolute left-0 top-full z-30 mt-1.5 max-h-72 w-80 overflow-y-auto rounded-xl border border-white/10 bg-black/80 p-1.5 text-xs backdrop-blur"
        >
          {families.map((group) => (
            <div key={group}>
              <p className="px-1.5 pb-0.5 pt-1.5 font-mono text-[10px] uppercase tracking-wider text-slate-500">
                {groupLabel(group)}
              </p>
              {sections
                .filter((s) => s.group === group)
                .map((s) => (
                  <button
                    key={s.id}
                    type="button"
                    role="option"
                    aria-selected={active?.id === s.id}
                    data-testid="memory-orb-section-option"
                    onClick={() => onJump(s)}
                    className={cn(
                      "flex w-full items-center gap-2 rounded-md px-1.5 py-1 text-left hover:bg-white/5",
                      active?.id === s.id && "bg-white/10",
                    )}
                  >
                    <span aria-hidden className="h-2 w-2 shrink-0 rounded-full" style={{ background: s.colour }} />
                    <span className="min-w-0 flex-1 truncate text-slate-200">
                      {title(s)}
                      {s.within && <span className="text-slate-500"> · {s.within}</span>}
                    </span>
                    <span className="shrink-0 font-mono text-slate-500">{s.memberIds.length}</span>
                  </button>
                ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
