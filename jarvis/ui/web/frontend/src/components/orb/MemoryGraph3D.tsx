/**
 * The memory orb's map in space: shaded spheres for every memory, family
 * cores and section branch points with their names, thin links, and a slow
 * turn. Cores and branch points are neutral; each memory has its own colour
 * (`lib/orbSections.ts`).
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
  type RefObject,
} from "react";
import ForceGraph3D from "react-force-graph-3d";
import type { ForceGraphMethods, NodeObject } from "react-force-graph-3d";
import {
  AmbientLight,
  Color,
  DirectionalLight,
  FogExp2,
  Group,
  Mesh,
  MeshPhongMaterial,
  PointLight,
  SphereGeometry,
} from "three";
import SpriteText from "three-spritetext";

import { useGraphAwake, type GraphEngineApi } from "@/hooks/useGraphAwake";
import { useWebglSurface } from "@/hooks/useWebglSurface";
import type { OrbGroup, OrbLink, OrbNode } from "@/lib/orbGraph";
import { SECTION_LABEL, SECTION_NEUTRAL, sectionFrameIds, type OrbSections } from "@/lib/orbSections";
import {
  buildSceneGraph,
  labelledHubs,
  familyRadius,
  sphereRadius,
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

const BACKDROP = "#05070b";
const FOG = 0x070a10;
const DIM = new Color("#1a212c");
const LINK_NOTE = "rgba(91, 212, 164, 0.42)";
const LINK_SOURCE = "rgba(223, 140, 255, 0.4)";
const LINK_FAMILY = "rgba(160, 172, 190, 0.14)";
const LINK_SECTION = "rgba(160, 172, 190, 0.3)";
const LINK_HOVER = "rgba(158, 236, 205, 0.9)";
const LINK_FADED = "rgba(91, 212, 164, 0.03)";
/** Degrees per second at the OrbitControls' speed of 1 is 6; this is ~2.4. */
const TURN_SPEED = 0.4;
const FLY_MS = 1100;

const sphere = new SphereGeometry(1, 22, 16);

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

/** Neutral for a core or a branch point; the memory's own colour otherwise. */
function colourOf(node: SceneNode, sections: OrbSections): string {
  if (node.core || node.branch) return SECTION_NEUTRAL;
  return sections.colourOf.get(node.id) ?? SECTION_NEUTRAL;
}

/** Space between a sphere and the name under it. */
function labelGap(node: SceneNode): number {
  return node.core ? 5 : node.branch ? 4 : 3;
}

const LINK_COLOUR: Record<SceneLink["kind"], string> = {
  note: LINK_NOTE,
  source: LINK_SOURCE,
  family: LINK_FAMILY,
  section: LINK_SECTION,
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
  const graphRef = useRef<ForceGraphMethods<SceneNode, SceneLink> | undefined>(undefined);
  const hostRef = useRef<HTMLDivElement | null>(null);
  const { generation } = useWebglSurface(hostRef);
  useGraphAwake(graphRef as unknown as RefObject<GraphEngineApi | undefined>);

  const reduced = useMemo(
    () => typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches,
    [],
  );

  const data = useMemo(() => buildSceneGraph(nodes, links, Math.random, sections), [nodes, links, sections]);
  const radius = familyRadius(nodes.length);
  const parts = useRef(new Map<string, NodeParts>());
  const hubs = useMemo(() => labelledHubs(data.nodes), [data]);
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
    const r = sphereRadius(node);
    const marker = node.core || node.branch;
    const material = new MeshPhongMaterial({
      color: colour,
      shininess: marker ? 70 : 45,
      specular: new Color("#3a4656"),
      emissive: new Color(colour).multiplyScalar(marker ? 0.22 : 0.12),
      transparent: true,
      opacity: 1,
    });
    const mesh = new Mesh(sphere, material);
    mesh.scale.setScalar(r);
    const holder = new Group();
    holder.add(mesh);
    let label: SpriteText | null = null;
    if (node.core) {
      label = makeLabel(groupLabelRef.current(node.group), SECTION_LABEL, 4.2, "700");
    } else if (node.branch) {
      const title = sections.byId.get(node.id)?.title ?? groupLabelRef.current(node.group);
      label = makeLabel(title, SECTION_LABEL, 3, "600");
    } else if (hubs.has(node.id) && node.node) {
      label = makeLabel(node.node.label, "#a3adbd", 2.6);
    }
    if (label) {
      label.position.set(0, -(r + labelGap(node)), 0);
      holder.add(label);
    }
    parts.current.set(node.id, { mesh, holder, label, extra: null });
    return holder;
  }, [hubs, sections]);

  // Colour, size and labels follow hover, pick and search without a rebuild.
  useEffect(() => {
    for (const node of data.nodes) {
      const p = parts.current.get(node.id);
      if (!p) continue;
      const own = node.node;
      const isHit = own ? hitIds.has(own.id) : false;
      const dim = searching && hitIds.size > 0 && !isHit;
      const active = node.branch === true && node.id === activeSectionId;
      const focus = (own !== undefined && (own.id === hoverId || own.id === pickedId)) || active;
      const base = new Color(colourOf(node, sections));
      const off = own !== undefined && !own.live;
      p.mesh.material.color.copy(dim ? DIM : base);
      const glow = node.core || node.branch ? 0.22 : 0.12;
      p.mesh.material.emissive.copy(base).multiplyScalar(dim ? 0 : focus ? 0.55 : glow);
      // Off (a disabled skill, a server that is off) is half there.
      p.mesh.material.opacity = dim ? 0.35 : off ? 0.45 : 1;
      const r = sphereRadius(node) * (active ? 1.3 : focus ? 1.6 : isHit ? 1.3 : 1);
      p.mesh.scale.setScalar(r);
      if (p.label) {
        p.label.visible = !dim;
        // A new colour redraws the label's texture, so only on a change.
        const ink = active ? "#ffffff" : node.core || node.branch ? SECTION_LABEL : "#a3adbd";
        if (p.label.color !== ink) p.label.color = ink;
        p.label.position.y = -(r + labelGap(node));
      }
      const wantExtra = own !== undefined && !p.label && (focus || (isHit && hitIds.size <= 40));
      if (wantExtra && !p.extra && own) {
        p.extra = makeLabel(own.label, "#e6eefb", 3, "600");
        p.holder.add(p.extra);
      }
      if (p.extra) p.extra.position.set(0, -(r + 3.5), 0);
      if (p.extra) p.extra.visible = wantExtra;
    }
  }, [data, sections, activeSectionId, hitIds, searching, hoverId, pickedId, generation]);

  const linkColor = useCallback(
    (link: SceneLink) => {
      const s = endpoint(link.source);
      const t = endpoint(link.target);
      if (hoverId !== null && (s === hoverId || t === hoverId)) return LINK_HOVER;
      if (searching && hitIds.size > 0 && !hitIds.has(s) && !hitIds.has(t)) return LINK_FADED;
      return LINK_COLOUR[link.kind];
    },
    [hitIds, hoverId, searching],
  );

  // Lights for shaded spheres, fog for depth, forces for a cloud with room in
  // it. Set again on a rebuilt scene, which starts from the library defaults.
  const configured = useRef(-1);
  const configure = useCallback(() => {
    const graph = graphRef.current;
    if (!graph || configured.current === generation) return;
    configured.current = generation;
    const key = new DirectionalLight(0xffffff, 2.4);
    key.position.set(1, 1.4, 1.2);
    const rim = new PointLight(0x5bd4a4, 1.2, 0, 0);
    rim.position.set(-radius * 2, -radius, -radius * 2);
    graph.lights([new AmbientLight(0x8fa3c0, 0.9), key, rim]);
    graph.scene().fog = new FogExp2(FOG, 0.24 / (radius * 1.4));
    const charge = graph.d3Force("charge") as { strength?: (v: number) => void; distanceMax?: (v: number) => void } | undefined;
    charge?.strength?.(-42);
    charge?.distanceMax?.(radius * 0.9);
    const link = graph.d3Force("link") as
      | { distance?: (f: (l: SceneLink) => number) => void; strength?: (f: (l: SceneLink) => number) => void }
      | undefined;
    link?.distance?.((l) =>
      l.kind === "family" ? 18 + radius * 0.1 : l.kind === "section" ? 9 + radius * 0.05 : l.kind === "source" ? 26 : 22,
    );
    // A member with no link of its own hangs close to its core or branch
    // point; one that is linked lets its links pull it, but a section keeps
    // a firmer hold than a family, and a link into another section pulls
    // only lightly (it is still drawn), so each section stays one cluster.
    const degree = new Map(data.nodes.map((n) => [n.id, n.degree]));
    const sectionOf = new Map(data.nodes.map((n) => [n.id, n.section]));
    link?.strength?.((l) => {
      const linked = (degree.get(endpoint(l.source)) ?? 0) > 0;
      if (l.kind === "family") return linked ? 0.25 : 0.6;
      if (l.kind === "section") return linked ? 0.6 : 0.9;
      return sectionOf.get(endpoint(l.source)) === sectionOf.get(endpoint(l.target)) ? 0.4 : 0.02;
    });
    const controls = graph.controls() as { autoRotate?: boolean; autoRotateSpeed?: number };
    controls.autoRotate = !reduced;
    controls.autoRotateSpeed = TURN_SPEED;
    graph.d3ReheatSimulation();
  }, [data, generation, radius, reduced]);

  useEffect(() => {
    configured.current = -1;
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
      const r = Math.max(only ? 30 : 60, reach[Math.min(reach.length - 1, Math.floor(reach.length * (only ? 1 : 0.95)))] ?? 0);
      const camera = graph.camera() as { position: { x: number; y: number; z: number }; fov?: number };
      const half = (((camera.fov ?? 40) * Math.PI) / 180) / 2;
      const horizontal = Math.atan(Math.tan(half) * (width / Math.max(height, 1)));
      const distance = (r / Math.sin(Math.min(half, horizontal))) * 1.08;
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
      const d = Math.max(120, radius * 2);
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
    const timers = [400, 1800, 4200].map((ms) => window.setTimeout(() => fit(), ms));
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
        warmupTicks={60}
        cooldownTicks={reduced ? 120 : 400}
        d3VelocityDecay={0.45}
        onEngineTick={configure}
        onNodeHover={(node) => {
          const own = (node as Node3D | null)?.node ?? null;
          onHover(own);
          if (hostRef.current) hostRef.current.style.cursor = node ? "pointer" : "";
        }}
        onNodeClick={(node) => {
          const n = node as Node3D;
          if (n.core) {
            fit(new Set(data.nodes.filter((m) => m.group === n.group).map((m) => m.id)));
            return;
          }
          const section = n.branch ? sections.byId.get(n.id) : undefined;
          if (section) {
            fit(sectionFrameIds(section));
            onSection?.(section.id);
            return;
          }
          if (n.node) {
            onPick(n.node);
            focus(n.id);
          }
        }}
        onBackgroundClick={() => onPick(null)}
      />
    </div>
  );
});

export default MemoryGraph3D;
