/**
 * The memory orb's map in space, laid out as a constellation
 * (`constellation` in `lib/orbGraph3d.ts`): gold topic hubs with their
 * names, each memory a sphere in its section's colour joined to its hub and
 * to memories with related names by thin lines, a starfield behind, and a
 * slow turn. Clicking a memory flies to it and lights its neighbours; the
 * Forces sliders set how far apart everything sits.
 *
 * Every node object is built once per data generation and then changed in
 * place (colour, size, label), so hovering, picking or searching never makes
 * the renderer rebuild the scene. Far nodes sink into the fog, which is what
 * gives the cloud its depth.
 *
 * Loaded lazily by MemoryOrbView, only while the map is shown in 3D. The
 * WebGL context is handed back on unmount and the scene rebuilt if the
 * browser takes it away (`useWebglSurface`).
 */
import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
  type RefObject,
} from "react";
import ForceGraph3D from "react-force-graph-3d";
import type { ForceGraphMethods, NodeObject } from "react-force-graph-3d";
import {
  AdditiveBlending,
  AmbientLight,
  BufferGeometry,
  Color,
  DirectionalLight,
  Float32BufferAttribute,
  FogExp2,
  Group,
  Mesh,
  MeshPhongMaterial,
  PointLight,
  Points,
  PointsMaterial,
  SphereGeometry,
} from "three";
import SpriteText from "three-spritetext";

import { useGraphAwake, type GraphEngineApi } from "@/hooks/useGraphAwake";
import { useT } from "@/i18n";
import { useWebglSurface } from "@/hooks/useWebglSurface";
import type { OrbGroup, OrbLink, OrbNode } from "@/lib/orbGraph";
import { SECTION_NEUTRAL, sectionFrameIds, type OrbSections } from "@/lib/orbSections";
import {
  buildSceneGraph,
  constellation,
  labelledHubs,
  familyRadius,
  type SceneLink,
  type SceneNode,
} from "@/lib/orbGraph3d";

export interface MemoryGraph3DHandle {
  /** Frame everything, or only these memories. */
  fit: (ids?: ReadonlySet<string>) => void;
  /** Fly to one memory and keep turning around it. */
  focus: (id: string) => void;
}

export interface MemoryGraph3DProps {
  nodes: OrbNode[];
  links: OrbLink[];
  /** The sections of `nodes` and each memory's colour. */
  sections: OrbSections;
  /** The section last jumped to; its branch point is lit. */
  activeSectionId: string | null;
  width: number;
  height: number;
  hitIds: ReadonlySet<string>;
  searching: boolean;
  hoverId: string | null;
  pickedId: string | null;
  groupLabel: (group: OrbGroup) => string;
  onHover: (node: OrbNode | null) => void;
  onPick: (node: OrbNode | null) => void;
  /** A section's branch point was clicked (the map has already framed it). */
  onSection?: (id: string) => void;
}

type Node3D = NodeObject<SceneNode> & SceneNode;

interface NodeParts {
  mesh: Mesh<SphereGeometry, MeshPhongMaterial>;
  holder: Group;
  label: SpriteText | null;
  /** A label added on hover, pick or search; hidden again afterwards. */
  extra: SpriteText | null;
}

const BACKDROP = "#04060a";
const FOG = 0x05080d;
const DIM = new Color("#1a212c");
/** Topic hubs and family cores. */
const HUB = "#f2b33d";
const HUB_LABEL = "#e8edf5";
const LINK_NOTE = "rgba(91, 212, 164, 0.5)";
const LINK_SOURCE = "rgba(223, 140, 255, 0.45)";
const LINK_SPOKE = "rgba(64, 196, 140, 0.24)";
const LINK_NEAR = "rgba(64, 196, 140, 0.1)";
const LINK_HOVER = "rgba(158, 236, 205, 0.95)";
const LINK_FADED = "rgba(91, 212, 164, 0.025)";
/** Hub names kept on at rest; the rest show theirs on hover. */
const NAMED_HUBS = 28;
/** Slider defaults: charge -(10 + 1.4 * repel), link length x (0.4 + linkLength / 50). */
const REPEL_DEFAULT = 55;
const LINK_DEFAULT = 40;
/** Degrees per second at the OrbitControls' speed of 1 is 6; this is ~2.4. */
const TURN_SPEED = 0.4;
const FLY_MS = 1100;

const sphere = new SphereGeometry(1, 18, 12);

/** Faint stars far behind the map: decoration, one draw call. */
function starfield(radius: number): Points {
  const count = 2400;
  const positions: number[] = [];
  for (let i = 0; i < count; i++) {
    // Evenly over a far shell (a Fibonacci lattice), at jittered depths.
    const y = 1 - (2 * (i + 0.5)) / count;
    const ring = Math.sqrt(1 - y * y);
    const a = i * Math.PI * (3 - Math.sqrt(5));
    const d = radius * (4 + ((i * 7919) % 100) / 40);
    positions.push(Math.cos(a) * ring * d, y * d, Math.sin(a) * ring * d);
  }
  const geometry = new BufferGeometry();
  geometry.setAttribute("position", new Float32BufferAttribute(positions, 3));
  const material = new PointsMaterial({
    color: 0x9fb4d8,
    size: 1.3,
    sizeAttenuation: false,
    transparent: true,
    opacity: 0.5,
    depthWrite: false,
    blending: AdditiveBlending,
    fog: false,
  });
  return new Points(geometry, material);
}

function endpoint(end: unknown): string {
  return typeof end === "object" && end !== null ? String((end as { id?: unknown }).id ?? "") : String(end ?? "");
}

function makeLabel(text: string, colour: string, height: number, weight = "500"): SpriteText {
  const sprite = new SpriteText(text.length > 42 ? `${text.slice(0, 41)}…` : text);
  sprite.color = colour;
  sprite.textHeight = height;
  sprite.fontWeight = weight;
  sprite.fontFace = "ui-sans-serif, system-ui, sans-serif";
  sprite.material.depthWrite = false;
  return sprite;
}

/** Gold for a hub; the section's colour for a memory, so a topic reads as one colour. */
function colourOf(node: SceneNode, sections: OrbSections): string {
  if (node.core || node.branch) return HUB;
  return (node.section && sections.byId.get(node.section)?.colour) || SECTION_NEUTRAL;
}

/** A hub grows with its members, a memory with its links. */
function radiusOf(node: SceneNode): number {
  if (node.core || node.branch) return Math.min(30, 14 + Math.sqrt(node.degree) * 1.2);
  return Math.min(13, 7.5 + Math.sqrt(node.degree) * 1.2);
}

/** Space between a sphere and the name under it. */
function labelGap(node: SceneNode): number {
  return node.core || node.branch ? 10 : 7;
}

const LINK_COLOUR: Record<SceneLink["kind"], string> = {
  note: LINK_NOTE,
  source: LINK_SOURCE,
  family: LINK_SPOKE,
  section: LINK_SPOKE,
  near: LINK_NEAR,
};

export const MemoryGraph3D = forwardRef<MemoryGraph3DHandle, MemoryGraph3DProps>(function MemoryGraph3D(
  {
    nodes,
    links,
    sections,
    activeSectionId,
    width,
    height,
    hitIds,
    searching,
    hoverId,
    pickedId,
    groupLabel,
    onHover,
    onPick,
    onSection,
  },
  ref,
) {
  const t = useT();
  const graphRef = useRef<ForceGraphMethods<SceneNode, SceneLink> | undefined>(undefined);
  const [repel, setRepel] = useState(REPEL_DEFAULT);
  const [linkLength, setLinkLength] = useState(LINK_DEFAULT);
  /** The memory or hub whose neighbourhood is lit; follows a pick from outside too. */
  const [litId, setLitId] = useState<string | null>(null);
  useEffect(() => setLitId(pickedId), [pickedId]);
  const hostRef = useRef<HTMLDivElement | null>(null);
  const { generation } = useWebglSurface(hostRef);
  useGraphAwake(graphRef as unknown as RefObject<GraphEngineApi | undefined>);

  const reduced = useMemo(
    () => typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches,
    [],
  );

  const data = useMemo(
    () => constellation(buildSceneGraph(nodes, links, Math.random, sections), sections),
    [nodes, links, sections],
  );
  const radius = familyRadius(nodes.length);
  const parts = useRef(new Map<string, NodeParts>());
  const hubs = useMemo(() => labelledHubs(data.nodes), [data]);
  // The biggest topic hubs keep their names on; hundreds at once is a wall of text.
  const named = useMemo(
    () =>
      new Set(
        data.nodes
          .filter((n) => n.core || n.branch)
          .sort((a, b) => b.degree - a.degree || a.id.localeCompare(b.id))
          .slice(0, NAMED_HUBS)
          .map((n) => n.id),
      ),
    [data],
  );
  const neighbours = useMemo(() => {
    const out = new Map<string, Set<string>>();
    const add = (a: string, b: string) => {
      const set = out.get(a);
      if (set) set.add(b);
      else out.set(a, new Set([b]));
    };
    for (const l of data.links) {
      const a = endpoint(l.source);
      const b = endpoint(l.target);
      add(a, b);
      add(b, a);
    }
    return out;
  }, [data]);
  const lit = useMemo(() => {
    if (!litId) return null;
    return new Set([litId, ...(neighbours.get(litId) ?? [])]);
  }, [litId, neighbours]);
  const groupLabelRef = useRef(groupLabel);
  groupLabelRef.current = groupLabel;

  // New data (or a rebuilt scene) starts with an empty set of node objects;
  // the old materials and label textures are released.
  useEffect(() => {
    const owned = parts.current;
    return () => {
      for (const p of owned.values()) {
        p.mesh.material.dispose();
        for (const sprite of [p.label, p.extra]) {
          sprite?.material.map?.dispose();
          sprite?.material.dispose();
        }
      }
      owned.clear();
    };
  }, [data, generation]);

  const nodeObject = useCallback((raw: NodeObject<SceneNode>) => {
    const node = raw as Node3D;
    const colour = colourOf(node, sections);
    const r = radiusOf(node);
    const marker = node.core || node.branch;
    const material = new MeshPhongMaterial({
      color: colour,
      shininess: 40,
      specular: new Color("#3a4656"),
      emissive: new Color(colour).multiplyScalar(marker ? 0.35 : 0.28),
      transparent: true,
      opacity: 1,
    });
    const mesh = new Mesh(sphere, material);
    mesh.scale.setScalar(r);
    const holder = new Group();
    holder.add(mesh);
    let label: SpriteText | null = null;
    if (node.core) {
      label = makeLabel(groupLabelRef.current(node.group), HUB_LABEL, 26, "700");
    } else if (node.branch && named.has(node.id)) {
      const title = sections.byId.get(node.id)?.title ?? groupLabelRef.current(node.group);
      label = makeLabel(title, HUB_LABEL, 22, "600");
    } else if (hubs.has(node.id) && node.node) {
      label = makeLabel(node.node.label, "#a3adbd", 11);
    }
    if (label) {
      label.position.set(0, -(r + labelGap(node)), 0);
      holder.add(label);
    }
    parts.current.set(node.id, { mesh, holder, label, extra: null });
    return holder;
  }, [hubs, named, sections]);

  // Colour, size and labels follow hover, pick and search without a rebuild.
  useEffect(() => {
    for (const node of data.nodes) {
      const p = parts.current.get(node.id);
      if (!p) continue;
      const own = node.node;
      const isHit = own ? hitIds.has(own.id) : false;
      const dim = (searching && hitIds.size > 0 && !isHit) || (lit !== null && !lit.has(node.id));
      const active = node.branch === true && (node.id === activeSectionId || node.id === litId);
      const focus = (own !== undefined && (own.id === hoverId || own.id === pickedId)) || active;
      const base = new Color(colourOf(node, sections));
      const off = own !== undefined && !own.live;
      p.mesh.material.color.copy(dim ? DIM : base);
      const glow = node.core || node.branch ? 0.35 : 0.28;
      p.mesh.material.emissive.copy(base).multiplyScalar(dim ? 0 : focus ? 0.7 : glow);
      // Off (a disabled skill, a server that is off) is half there.
      p.mesh.material.opacity = dim ? 0.22 : off ? 0.45 : 1;
      const r = radiusOf(node) * (active ? 1.3 : focus ? 1.6 : isHit ? 1.3 : 1);
      p.mesh.scale.setScalar(r);
      if (p.label) {
        p.label.visible = !dim;
        // A new colour redraws the label's texture, so only on a change.
        const ink = active ? "#ffffff" : node.core || node.branch ? HUB_LABEL : "#a3adbd";
        if (p.label.color !== ink) p.label.color = ink;
        p.label.position.y = -(r + labelGap(node));
      }
      // A lit neighbourhood names everyone in it, as long as it stays readable.
      const litName = lit !== null && lit.has(node.id) && lit.size <= 40;
      const wantExtra = !p.label && (focus || litName || (own !== undefined && isHit && hitIds.size <= 40));
      const name = own?.label ?? (node.branch ? sections.byId.get(node.id)?.title : null) ?? null;
      if (wantExtra && !p.extra && name) {
        p.extra = makeLabel(name, "#e6eefb", node.branch ? 22 : 12, "600");
        p.holder.add(p.extra);
      }
      if (p.extra) p.extra.position.set(0, -(r + 6), 0);
      if (p.extra) p.extra.visible = wantExtra;
    }
  }, [data, sections, activeSectionId, hitIds, searching, hoverId, pickedId, lit, litId, generation]);

  const linkColor = useCallback(
    (link: SceneLink) => {
      const s = endpoint(link.source);
      const e = endpoint(link.target);
      if (hoverId !== null && (s === hoverId || e === hoverId)) return LINK_HOVER;
      if (litId !== null) return s === litId || e === litId ? LINK_HOVER : LINK_FADED;
      if (searching && hitIds.size > 0 && !hitIds.has(s) && !hitIds.has(e)) return LINK_FADED;
      return LINK_COLOUR[link.kind];
    },
    [hitIds, hoverId, litId, searching],
  );

  // Lights, fog for depth and the starfield, set once per scene (a rebuilt
  // scene starts from the library defaults).
  const configured = useRef(-1);
  const configure = useCallback(() => {
    const graph = graphRef.current;
    if (!graph || configured.current === generation) return;
    configured.current = generation;
    const key = new DirectionalLight(0xffffff, 2.2);
    key.position.set(1, 1.4, 1.2);
    const rim = new PointLight(0x5bd4a4, 1.1, 0, 0);
    rim.position.set(-radius * 2, -radius, -radius * 2);
    graph.lights([new AmbientLight(0x8fa3c0, 1), key, rim]);
    graph.scene().fog = new FogExp2(FOG, 0.1 / (radius * 1.4));
    graph.scene().add(starfield(radius));
    const controls = graph.controls() as { autoRotate?: boolean; autoRotateSpeed?: number };
    controls.autoRotate = !reduced;
    controls.autoRotateSpeed = TURN_SPEED;
  }, [generation, radius, reduced]);

  // Forces: hubs push apart, a memory sits on a short line from its hub, and a
  // name-sharing link is long and weak, so it draws topics together without
  // packing them into one ball. The sliders change these live, once the
  // engine has its layout (the first tick).
  const applied = useRef("");
  const applyForces = useCallback(() => {
    const graph = graphRef.current;
    const want = `${generation}|${repel}|${linkLength}`;
    if (!graph || applied.current === want) return;
    applied.current = want;
    const charge = graph.d3Force("charge") as { strength?: (v: number) => void; distanceMax?: (v: number) => void } | undefined;
    charge?.strength?.(-(10 + repel * 1.4));
    charge?.distanceMax?.(radius * 4);
    const scale = 0.4 + linkLength / 50;
    const link = graph.d3Force("link") as
      | { distance?: (f: (l: SceneLink) => number) => void; strength?: (f: (l: SceneLink) => number) => void }
      | undefined;
    link?.distance?.((l) => scale * (l.kind === "near" ? 60 : l.kind === "section" || l.kind === "family" ? 20 : 30));
    link?.strength?.((l) => (l.kind === "near" ? 0.015 : l.kind === "section" || l.kind === "family" ? 0.8 : 0.3));
    graph.d3ReheatSimulation();
  }, [generation, repel, linkLength, radius]);
  const ticked = useRef(-1);
  const onTick = useCallback(() => {
    configure();
    ticked.current = generation;
    applyForces();
  }, [configure, applyForces, generation]);
  useEffect(() => {
    if (ticked.current === generation) applyForces();
  }, [applyForces, generation]);

  useEffect(() => {
    configured.current = -1;
    applied.current = "";
  }, [data]);

  /**
   * Stand back far enough to fill the frame with these nodes, looking from
   * where the camera already is. The whole map is framed on its 95th
   * percentile, so a few far-flung nodes do not shrink everything else.
   */
  const fit = useCallback(
    (ids?: ReadonlySet<string>) => {
      const graph = graphRef.current;
      if (!graph) return;
      const only = ids && ids.size > 0;
      const placed = (data.nodes as Node3D[]).filter(
        (n) => n.x !== undefined && (!only || ids.has(n.id)),
      );
      if (!placed.length) return;
      const centre = { x: 0, y: 0, z: 0 };
      for (const n of placed) {
        centre.x += (n.x ?? 0) / placed.length;
        centre.y += (n.y ?? 0) / placed.length;
        centre.z += (n.z ?? 0) / placed.length;
      }
      const reach = placed
        .map((n) => Math.hypot((n.x ?? 0) - centre.x, (n.y ?? 0) - centre.y, (n.z ?? 0) - centre.z))
        .sort((a, b) => a - b);
      const r = Math.max(only ? 30 : 60, reach[Math.min(reach.length - 1, Math.floor(reach.length * (only ? 1 : 0.6)))] ?? 0);
      const camera = graph.camera() as { position: { x: number; y: number; z: number }; fov?: number };
      const half = (((camera.fov ?? 40) * Math.PI) / 180) / 2;
      const horizontal = Math.atan(Math.tan(half) * (width / Math.max(height, 1)));
      const distance = (r / Math.sin(Math.min(half, horizontal))) * 1.08;
      // The fog was sized from the node count, not from how far the fitted
      // camera ends up: with a few thousand imported conversations the fit
      // at 1.8 s pulled the camera back and the whole orb faded into the fog
      // (live 2026-10-26). Keep the fog light at the distance actually used.
      const fog = graph.scene().fog as { density?: number } | null;
      if (fog && typeof fog.density === "number") fog.density = 0.35 / Math.max(distance + r, 1);
      const target = (graph.controls() as { target?: { x: number; y: number; z: number } }).target ?? centre;
      const away = {
        x: camera.position.x - target.x,
        y: camera.position.y - target.y,
        z: camera.position.z - target.z,
      };
      const len = Math.hypot(away.x, away.y, away.z);
      const dir = len > 1e-3 ? { x: away.x / len, y: away.y / len, z: away.z / len } : { x: 0, y: 0.3, z: 0.95 };
      graph.cameraPosition(
        { x: centre.x + dir.x * distance, y: centre.y + dir.y * distance, z: centre.z + dir.z * distance },
        centre,
        reduced ? 0 : 900,
      );
    },
    [data, width, height, reduced],
  );

  const focus = useCallback(
    (id: string) => {
      const graph = graphRef.current;
      const node = data.nodes.find((n) => n.id === id) as Node3D | undefined;
      if (!graph || !node || node.x === undefined) return;
      const at = { x: node.x ?? 0, y: node.y ?? 0, z: node.z ?? 0 };
      // Come in from where the camera already is, so the fly-in never flips
      // the map over.
      const cam = graph.camera().position;
      const away = { x: cam.x - at.x, y: cam.y - at.y, z: cam.z - at.z };
      const len = Math.hypot(away.x, away.y, away.z) || 1;
      const d = 260;
      graph.cameraPosition(
        { x: at.x + (away.x / len) * d, y: at.y + (away.y / len) * d, z: at.z + (away.z / len) * d },
        at,
        reduced ? 0 : FLY_MS,
      );
    },
    [data, radius, reduced],
  );

  useImperativeHandle(ref, () => ({ fit, focus }), [fit, focus]);

  // Frame once the cloud has spread, and again when it has settled.
  useEffect(() => {
    if (!data.nodes.length) return;
    const timers = [400, 1800, 4200, 8000].map((ms) => window.setTimeout(() => fit(), ms));
    return () => timers.forEach(window.clearTimeout);
  }, [data, fit, generation, width, height]);

  return (
    <div ref={hostRef} className="absolute inset-0" data-testid="memory-orb-3d">
      <ForceGraph3D<SceneNode, SceneLink>
        key={generation}
        ref={graphRef}
        graphData={data}
        width={width}
        height={height}
        backgroundColor={BACKDROP}
        showNavInfo={false}
        controlType="orbit"
        nodeId="id"
        nodeThreeObject={nodeObject}
        nodeLabel={() => ""}
        linkColor={linkColor}
        linkOpacity={1}
        linkWidth={0}
        enableNodeDrag={false}
        warmupTicks={30}
        cooldownTicks={reduced ? 120 : 300}
        d3VelocityDecay={0.4}
        onEngineTick={onTick}
        onNodeHover={(node) => {
          const own = (node as Node3D | null)?.node ?? null;
          onHover(own);
          if (hostRef.current) hostRef.current.style.cursor = node ? "pointer" : "";
        }}
        onNodeClick={(node) => {
          const n = node as Node3D;
          if (n.core) {
            setLitId(n.id);
            fit(new Set(data.nodes.filter((m) => m.group === n.group).map((m) => m.id)));
            return;
          }
          const section = n.branch ? sections.byId.get(n.id) : undefined;
          if (section) {
            setLitId(n.id);
            fit(sectionFrameIds(section));
            onSection?.(section.id);
            return;
          }
          if (n.node) {
            setLitId(n.id);
            onPick(n.node);
            focus(n.id);
          }
        }}
        onBackgroundClick={() => {
          setLitId(null);
          onPick(null);
        }}
      />
      <div
        className="absolute bottom-4 left-4 w-44 rounded-lg border border-white/10 bg-black/55 px-3 py-2 text-[10px] text-slate-300 backdrop-blur"
        data-testid="memory-orb-forces"
      >
        <p className="mb-1 font-mono uppercase tracking-[0.18em] text-slate-500">{t("orb2.memory.forces")}</p>
        <label className="flex items-center justify-between gap-2">
          <span>{t("orb2.memory.repel")}</span>
          <input
            type="range"
            min={0}
            max={100}
            value={repel}
            onChange={(e) => setRepel(Number(e.target.value))}
            className="w-20 accent-emerald-400"
            data-testid="memory-orb-repel"
          />
        </label>
        <label className="mt-1 flex items-center justify-between gap-2">
          <span>{t("orb2.memory.link_length")}</span>
          <input
            type="range"
            min={0}
            max={100}
            value={linkLength}
            onChange={(e) => setLinkLength(Number(e.target.value))}
            className="w-20 accent-emerald-400"
            data-testid="memory-orb-link-length"
          />
        </label>
      </div>
    </div>
  );
});

export default MemoryGraph3D;
