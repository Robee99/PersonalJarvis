/**
 * The Orb's project intelligence: every project of the brain map
 * (`/api/brain-map`) as a hub on the map, coloured by its health and ringed
 * by its measured progress, with the project list on the left and the opened
 * project on the right.
 *
 * Opening a project spreads its items around its hub, grouped by branch kind
 * and capped per kind (`projectView.buildProjectMap`); clicking one opens its
 * branch in the panel. Everything here is drawn from what the API returned:
 * no colour, count or progress is computed in the browser.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import ForceGraph2D from "react-force-graph-2d";
import type { ForceGraphMethods, NodeObject } from "react-force-graph-2d";

import { fill, useT } from "@/i18n";

import { HEALTH_HEX, fetchProject, fetchProjects, reindexBrainMap, type Project } from "./brainMap";
import { graphHandController } from "./hands/graphController";
import type { HandController } from "./hands/useHandTracking";
import { ProjectPanel, type PanelTab } from "./ProjectPanel";
import { ProjectRail } from "./ProjectRail";
import { buildProjectMap, filterProjects, type MapNode, type ProjectFilter } from "./projectView";

type DrawNode = NodeObject<MapNode> & MapNode;

const HUB_RADIUS = 7;
const ITEM_RADIUS = 2.6;
const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

function useSize(ref: React.RefObject<HTMLDivElement>) {
  const [size, setSize] = useState({ width: 0, height: 0 });
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

export interface ProjectsOrbProps {
  /** Receives the controller hand gestures drive while this map is shown. */
  registerHands?(controller: HandController | null): void;
}

export function ProjectsOrb({ registerHands }: ProjectsOrbProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const wrapRef = useRef<HTMLDivElement>(null);
  const graphRef = useRef<ForceGraphMethods<DrawNode> | undefined>(undefined);
  const { width, height } = useSize(wrapRef);
  const [filter, setFilter] = useState<ProjectFilter>({ query: "", colours: new Set(), sort: "priority" });
  const settledQuery = useDebounced(filter.query, 150);
  const [openId, setOpenId] = useState<string | null>(null);
  const [branchId, setBranchId] = useState<string | null>(null);
  const [tab, setTab] = useState<PanelTab>("overview");
  const [hoverId, setHoverId] = useState<string | null>(null);

  const projectsQuery = useQuery({ queryKey: ["orb2", "projects"], queryFn: fetchProjects, staleTime: 30_000 });
  const all = useMemo(() => projectsQuery.data?.projects ?? [], [projectsQuery.data]);
  const rules = useMemo(() => projectsQuery.data?.health_rules ?? [], [projectsQuery.data]);
  const shownProjects = useMemo(
    () => filterProjects(all, { ...filter, query: settledQuery }),
    [all, filter, settledQuery],
  );
  const opened = all.find((p) => p.id === openId) ?? null;
  const detailQuery = useQuery({
    queryKey: ["orb2", "project", openId],
    queryFn: () => fetchProject(openId as string),
    enabled: !!openId,
    staleTime: 20_000,
  });

  const reindex = useMutation({
    mutationFn: reindexBrainMap,
    onSettled: () => void queryClient.invalidateQueries({ queryKey: ["orb2"] }),
  });

  const map = useMemo(
    () => buildProjectMap(shownProjects, openId ? { id: openId, thread: detailQuery.data?.thread } : undefined),
    [shownProjects, openId, detailQuery.data],
  );
  // The force engine mutates what it is given; positions survive a rebuild
  // by id so opening a project does not scatter every other hub.
  const positions = useRef(new Map<string, { x?: number; y?: number }>());
  const graphData = useMemo(() => {
    const nodes = map.nodes.map((n) => ({ ...n, ...positions.current.get(n.id) })) as DrawNode[];
    return { nodes, links: map.links.map((l) => ({ ...l })) };
  }, [map]);
  useEffect(
    () => () => {
      for (const n of graphData.nodes) positions.current.set(n.id, { x: n.x, y: n.y });
    },
    [graphData],
  );

  useEffect(() => {
    const fg = graphRef.current;
    if (!fg) return;
    fg.d3Force("charge")?.strength((n: DrawNode) => (n.type === "project" ? -160 : -18));
    fg.d3Force("link")?.distance((l: { source: DrawNode }) => (l.source.type === "cluster" ? 46 : 34));
    fg.d3ReheatSimulation();
  }, [graphData]);

  const open = useCallback((project: Project | null) => {
    setOpenId(project?.id ?? null);
    setBranchId(null);
    setTab("overview");
  }, []);

  // Frame the opened project once its items are on the map.
  useEffect(() => {
    if (!openId || !detailQuery.data) return;
    const id = window.setTimeout(() => {
      const fg = graphRef.current;
      if (!fg) return;
      fg.zoomToFit(600, 120, (n: DrawNode) => n.projectId === openId);
    }, 450);
    return () => window.clearTimeout(id);
  }, [openId, detailQuery.data]);

  const clickNode = useCallback(
    (node: DrawNode) => {
      if (node.type === "project") {
        if (node.id !== openId) open(all.find((p) => p.id === node.id) ?? null);
        return;
      }
      if (node.type === "cluster") {
        setBranchId(null);
        setTab("items");
        return;
      }
      setBranchId(node.id);
    },
    [all, open, openId],
  );

  // Hands drive the same handlers as the mouse.
  const latest = useRef({ graphData, width, height, openId, branchId, clickNode });
  latest.current = { graphData, width, height, openId, branchId, clickNode };
  useEffect(() => {
    if (!registerHands) return;
    registerHands(
      graphHandController<DrawNode>({
        graph: () => graphRef.current as never,
        size: () => ({ width: latest.current.width, height: latest.current.height }),
        nodes: () => latest.current.graphData.nodes,
        picked: () => {
          const id = latest.current.branchId ?? latest.current.openId;
          return latest.current.graphData.nodes.find((n) => n.id === id) ?? null;
        },
        hover: (node) => setHoverId(node?.id ?? null),
        pick: (node) => (node ? latest.current.clickNode(node) : undefined),
        open: (node) => {
          // A second pinch on what is already open goes one level deeper.
          if (node.type === "project") setTab("thread");
          else latest.current.clickNode(node);
        },
      }),
    );
    return () => registerHands(null);
  }, [registerHands]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || (e.target as HTMLElement | null)?.closest("input,textarea")) return;
      if (branchId) setBranchId(null);
      else if (openId) open(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [branchId, openId, open]);

  const hovered = graphData.nodes.find((n) => n.id === hoverId) ?? null;
  const hoveredProject = hovered?.type === "project" ? all.find((p) => p.id === hovered.id) : undefined;

  const drawNode = (node: DrawNode, ctx: CanvasRenderingContext2D, scale: number) => {
    const x = node.x ?? 0;
    const y = node.y ?? 0;
    const focus = node.id === hoverId || node.id === openId || node.id === branchId;
    const dim = !!openId && node.projectId !== openId;
    ctx.globalAlpha = dim ? 0.35 : 1;
    if (node.type === "project") {
      const r = HUB_RADIUS * node.weight;
      ctx.lineWidth = Math.max(1.4, 2.2 / Math.sqrt(scale));
      ctx.strokeStyle = node.colour;
      // Faint full ring, bright arc for the measured progress.
      ctx.globalAlpha = dim ? 0.15 : 0.28;
      ctx.beginPath();
      ctx.arc(x, y, r, 0, 2 * Math.PI);
      ctx.stroke();
      ctx.globalAlpha = dim ? 0.35 : 1;
      if (node.progress !== null && node.progress > 0) {
        ctx.save();
        ctx.shadowColor = node.colour;
        ctx.shadowBlur = focus ? 16 : 8;
        ctx.beginPath();
        ctx.arc(x, y, r, -Math.PI / 2, -Math.PI / 2 + 2 * Math.PI * node.progress);
        ctx.stroke();
        ctx.restore();
      }
      ctx.beginPath();
      ctx.arc(x, y, r * 0.42, 0, 2 * Math.PI);
      ctx.fillStyle = node.colour;
      ctx.fill();
      if (focus) {
        ctx.setLineDash([2 / scale, 3 / scale]);
        ctx.strokeStyle = "rgba(125,211,252,0.8)";
        ctx.lineWidth = 1 / scale;
        ctx.beginPath();
        ctx.arc(x, y, r + 4, 0, 2 * Math.PI);
        ctx.stroke();
        ctx.setLineDash([]);
      }
      if (focus || scale > 0.9 || node.id === openId) {
        const size = Math.max(10 / scale, 2.5);
        ctx.font = `600 ${size}px ${MONO}`;
        ctx.textAlign = "center";
        ctx.textBaseline = "top";
        ctx.fillStyle = "rgba(214,240,255,0.9)";
        const label = node.label.length > 26 ? `${node.label.slice(0, 25)}…` : node.label;
        ctx.fillText(label.toUpperCase(), x, y + r + 3);
      }
    } else if (node.type === "cluster") {
      ctx.setLineDash([2, 2]);
      ctx.strokeStyle = node.colour;
      ctx.lineWidth = 0.8;
      ctx.beginPath();
      ctx.arc(x, y, 6, 0, 2 * Math.PI);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.font = `600 4px ${MONO}`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillStyle = node.colour;
      ctx.fillText(node.label, x, y);
    } else {
      const r = focus ? ITEM_RADIUS * 1.6 : ITEM_RADIUS;
      ctx.beginPath();
      ctx.arc(x, y, r, 0, 2 * Math.PI);
      if (node.suggested) {
        ctx.strokeStyle = node.colour;
        ctx.lineWidth = 0.8;
        ctx.stroke();
      } else {
        ctx.fillStyle = node.colour;
        ctx.fill();
      }
      if (focus || scale > 3) {
        const size = Math.max(9 / scale, 2);
        ctx.font = `${size}px ui-sans-serif, system-ui`;
        ctx.textAlign = "center";
        ctx.textBaseline = "top";
        ctx.fillStyle = "rgba(225,235,255,0.85)";
        const label = node.label.length > 40 ? `${node.label.slice(0, 39)}…` : node.label;
        ctx.fillText(label, x, y + r + 1.5);
      }
    }
    ctx.globalAlpha = 1;
  };

  return (
    <div className="absolute inset-0" data-testid="orb2-projects">
      <div ref={wrapRef} className="absolute inset-0">
        {width > 0 && (
          <ForceGraph2D
            ref={graphRef}
            graphData={graphData}
            width={width}
            height={height}
            backgroundColor="rgba(0,0,0,0)"
            nodeId="id"
            nodeCanvasObject={drawNode}
            nodePointerAreaPaint={(node: DrawNode, colour, ctx) => {
              ctx.fillStyle = colour;
              ctx.beginPath();
              ctx.arc(node.x ?? 0, node.y ?? 0, node.type === "project" ? HUB_RADIUS * node.weight + 3 : 6, 0, 2 * Math.PI);
              ctx.fill();
            }}
            linkColor={() => "rgba(79,209,232,0.22)"}
            linkWidth={0.6}
            cooldownTicks={160}
            onNodeHover={(node) => setHoverId((node as DrawNode | null)?.id ?? null)}
            onNodeClick={(node) => clickNode(node as DrawNode)}
          />
        )}
      </div>

      <div className="pointer-events-none absolute bottom-24 left-4 top-[128px] flex w-80 max-w-[calc(100%-2rem)] flex-col">
        <ProjectRail
          all={all}
          shown={shownProjects}
          rules={rules}
          filter={filter}
          onFilter={setFilter}
          openId={openId}
          hoverId={hoverId}
          onOpen={(p) => open(p)}
          onHover={(p) => setHoverId(p?.id ?? null)}
          onReindex={() => reindex.mutate()}
          reindexing={reindex.isPending}
          loading={projectsQuery.isLoading}
          failed={projectsQuery.isError}
        />
        {rules.length > 0 && (
          <details className="pointer-events-auto mt-2 rounded-xl border border-white/10 bg-black/55 px-3 py-2 text-[11px] text-slate-400 backdrop-blur">
            <summary className="cursor-pointer font-mono text-[10px] uppercase tracking-[0.2em] text-slate-400">
              {t("orb2.legend.title")}
            </summary>
            <ol className="mt-1.5 space-y-1" data-testid="orb2-legend">
              {rules.map((rule) => (
                <li key={rule.id} className="flex gap-1.5 leading-4">
                  <span
                    className="mt-1 h-2 w-2 shrink-0 rounded-full"
                    style={{ background: HEALTH_HEX[rule.colour] }}
                  />
                  <span>
                    <span className="text-slate-200">{t(`orb2.health.rule.${rule.id}`)}</span> — {rule.when}
                  </span>
                </li>
              ))}
            </ol>
          </details>
        )}
        {map.hiddenProjects > 0 && (
          <p className="mt-1 px-1 font-mono text-[10px] text-slate-500">
            {fill(t("orb2.map.hidden_projects"), { count: map.hiddenProjects })}
          </p>
        )}
      </div>

      {hoveredProject && hoveredProject.id !== openId && (
        <div className="pointer-events-none absolute bottom-24 left-[22rem] max-w-xs rounded-xl border border-white/10 bg-black/75 p-2.5 text-xs backdrop-blur">
          <p className="font-semibold text-slate-100">{hoveredProject.title}</p>
          <p className="mt-0.5" style={{ color: HEALTH_HEX[hoveredProject.health.colour] }}>
            {hoveredProject.health.reason}
          </p>
        </div>
      )}

      {opened && (
        <div className="pointer-events-none absolute bottom-24 right-4 top-16 flex w-[26rem] max-w-[calc(100%-2rem)] flex-col">
          <ProjectPanel
            project={opened}
            rules={rules}
            branchId={branchId}
            onBranch={setBranchId}
            onClose={() => open(null)}
            tab={tab}
            onTab={setTab}
          />
        </div>
      )}
    </div>
  );
}
