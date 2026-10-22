import { Component, useEffect, useLayoutEffect, useMemo, useRef, type ReactNode } from "react";
import { Canvas, useFrame, useThree, type ThreeEvent } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import type { OrbitControls as OrbitControlsImpl } from "three-stdlib";
import {
  AdditiveBlending,
  BufferGeometry,
  Color,
  Float32BufferAttribute,
  Object3D,
  Vector3,
  type InstancedMesh,
  type Mesh,
  type MeshBasicMaterial,
} from "three";

import { ORB_HEX, type OrbLayout, type PlacedNode, type Vec3 } from "@/lib/memoryOrbLayout";
import { readVoiceInputLevel } from "@/lib/voiceInputLevel";
import { voiceOutputLevelRef } from "@/lib/voiceOutputLevel";

/**
 * The memory orb's WebGL scene — loaded lazily by `MemoryOrb3D`, which owns
 * the data, the focus and every word on screen. This module only draws:
 *
 *  - every node as a small glowing sphere in its work colour (one instanced
 *    mesh for the spheres, one for their halos — two draw calls whatever
 *    the size of the memory);
 *  - a thin line from each node to its parent, top-level communities to the
 *    core, so the GraphRAG hierarchy reads as branches;
 *  - a translucent core that swells with the live voice: the louder of the
 *    microphone (`lib/voiceInputLevel`) and the assistant's playback
 *    (`lib/voiceOutputLevel`), the same rule as the home stage's light;
 *  - the focused node, ringed, with the camera easing round to face it.
 *
 * Drag rotates (OrbitControls), the wheel zooms; left alone the orb turns
 * slowly by itself. Frames stop while the panel is off screen (`awake`).
 */

const NODE_RADIUS = 0.028;
const CORE_RADIUS = 0.34;
const CAMERA_DISTANCE = 4.2;
const FOCUS_DISTANCE = 3.3;
/** How long after the last touch or focus change the idle rotation resumes. */
const IDLE_AFTER_MS = 9_000;
/** Camera easing rate toward a focused node, per second. */
const EASE_RATE = 3.2;
const CORE_HEX = "#4fd1e8";

function nodeScale(node: PlacedNode): number {
  if (node.level === 3) return 1;
  const weight = Math.min(1.2, Math.log10(node.size + 1) * 0.6);
  return (node.level === 1 ? 2.1 : 1.55) + weight;
}

export interface MemoryOrbSceneProps {
  layout: OrbLayout;
  focusedId: string | null;
  hoverId: string | null;
  onHover: (id: string | null) => void;
  onPick: (id: string | null) => void;
  /** The panel is on screen; frames stop when it is not. */
  awake: boolean;
  reduced: boolean;
  /** The context died or the renderer threw: the shell shows its fallback. */
  onUnavailable: () => void;
}

export default function MemoryOrbScene(props: MemoryOrbSceneProps) {
  const { awake, reduced, onUnavailable, onPick } = props;
  return (
    <RenderBoundary onFail={onUnavailable}>
      <Canvas
        data-testid="memory-orb-canvas"
        camera={{ position: [0, 0.8, CAMERA_DISTANCE], fov: 42, near: 0.1, far: 50 }}
        dpr={[1, 1.5]}
        gl={{ antialias: true, alpha: true, powerPreference: "low-power" }}
        frameloop={!awake ? "never" : reduced ? "demand" : "always"}
        onPointerMissed={() => onPick(null)}
        onCreated={({ gl }) => {
          gl.domElement.addEventListener("webglcontextlost", onUnavailable, { once: true });
        }}
      >
        <OrbContent {...props} />
      </Canvas>
    </RenderBoundary>
  );
}

function OrbContent({ layout, focusedId, hoverId, onHover, onPick, reduced, awake }: MemoryOrbSceneProps) {
  const controls = useRef<OrbitControlsImpl>(null);
  const lastTouch = useRef(0);
  const focused = focusedId ? (layout.byId.get(focusedId) ?? null) : null;
  return (
    <>
      <Branches layout={layout} />
      <NodeCloud
        nodes={layout.nodes}
        focusedId={focusedId}
        hoverId={hoverId}
        onHover={onHover}
        onPick={onPick}
      />
      <Core reduced={reduced} />
      {focused && <FocusRing pos={focused.pos} scale={nodeScale(focused)} />}
      <CameraRig target={focused?.pos ?? null} controls={controls} lastTouch={lastTouch} reduced={reduced} />
      <OrbitControls
        ref={controls}
        makeDefault
        enabled={awake}
        enablePan={false}
        enableDamping={!reduced}
        dampingFactor={0.08}
        rotateSpeed={0.6}
        zoomSpeed={0.6}
        minDistance={2.2}
        maxDistance={7}
        autoRotateSpeed={0.45}
        onStart={() => {
          lastTouch.current = performance.now();
        }}
      />
    </>
  );
}

/** Spheres and halos, one instance per node. Colours and sizes are set here, not per frame. */
function NodeCloud({
  nodes,
  focusedId,
  hoverId,
  onHover,
  onPick,
}: {
  nodes: PlacedNode[];
  focusedId: string | null;
  hoverId: string | null;
  onHover: (id: string | null) => void;
  onPick: (id: string | null) => void;
}) {
  const core = useRef<InstancedMesh>(null);
  const halo = useRef<InstancedMesh>(null);
  const { invalidate, gl } = useThree();

  useLayoutEffect(() => {
    const meshes = [core.current, halo.current];
    if (meshes.some((m) => !m)) return;
    const dummy = new Object3D();
    const colour = new Color();
    nodes.forEach((node, i) => {
      const boost = node.id === focusedId ? 1.8 : node.id === hoverId ? 1.4 : 1;
      dummy.position.set(node.pos[0], node.pos[1], node.pos[2]);
      dummy.scale.setScalar(nodeScale(node) * boost);
      dummy.updateMatrix();
      colour.set(ORB_HEX[node.colour]);
      for (const mesh of meshes) {
        mesh!.setMatrixAt(i, dummy.matrix);
        mesh!.setColorAt(i, colour);
      }
    });
    for (const mesh of meshes) {
      mesh!.instanceMatrix.needsUpdate = true;
      if (mesh!.instanceColor) mesh!.instanceColor.needsUpdate = true;
      // Raycasting tests against the bounds; they moved with the matrices.
      mesh!.boundingSphere = null;
    }
    invalidate();
  }, [nodes, focusedId, hoverId, invalidate]);

  const nodeAt = (e: ThreeEvent<PointerEvent | MouseEvent>) =>
    e.instanceId === undefined ? null : (nodes[e.instanceId] ?? null);

  return (
    <>
      <instancedMesh
        key={`core-${nodes.length}`}
        ref={core}
        args={[undefined, undefined, nodes.length]}
        onPointerMove={(e) => {
          e.stopPropagation();
          const node = nodeAt(e);
          onHover(node?.id ?? null);
          gl.domElement.style.cursor = node ? "pointer" : "";
        }}
        onPointerOut={() => {
          onHover(null);
          gl.domElement.style.cursor = "";
        }}
        onClick={(e) => {
          // A drag that ends on a node is a rotation, not a pick.
          if (e.delta > 4) return;
          e.stopPropagation();
          onPick(nodeAt(e)?.id ?? null);
        }}
      >
        <sphereGeometry args={[NODE_RADIUS, 12, 10]} />
        <meshBasicMaterial toneMapped={false} />
      </instancedMesh>
      <instancedMesh
        key={`halo-${nodes.length}`}
        ref={halo}
        args={[undefined, undefined, nodes.length]}
        raycast={() => undefined}
      >
        <sphereGeometry args={[NODE_RADIUS * 2.4, 10, 8]} />
        <meshBasicMaterial
          transparent
          opacity={0.16}
          depthWrite={false}
          blending={AdditiveBlending}
          toneMapped={false}
        />
      </instancedMesh>
    </>
  );
}

/** Every node to its parent; the top level to the core's surface. One draw call. */
function Branches({ layout }: { layout: OrbLayout }) {
  const geometry = useMemo(() => {
    const positions: number[] = [];
    const colours: number[] = [];
    const child = new Color();
    const base = new Color();
    for (const node of layout.nodes) {
      const parent = node.parent ? layout.byId.get(node.parent) : undefined;
      const from: Vec3 = parent
        ? parent.pos
        : [node.pos[0] * CORE_RADIUS, node.pos[1] * CORE_RADIUS, node.pos[2] * CORE_RADIUS];
      positions.push(...from, ...node.pos);
      child.set(ORB_HEX[node.colour]);
      // Bright at the node, fading toward the trunk.
      base.copy(child).multiplyScalar(0.35);
      colours.push(base.r, base.g, base.b, child.r, child.g, child.b);
    }
    const g = new BufferGeometry();
    g.setAttribute("position", new Float32BufferAttribute(positions, 3));
    g.setAttribute("color", new Float32BufferAttribute(colours, 3));
    return g;
  }, [layout]);
  useEffect(() => () => geometry.dispose(), [geometry]);
  return (
    <lineSegments geometry={geometry} raycast={() => undefined}>
      <lineBasicMaterial vertexColors transparent opacity={0.45} depthWrite={false} toneMapped={false} />
    </lineSegments>
  );
}

/** The core: breathes at rest, swells with whoever is speaking. */
function Core({ reduced }: { reduced: boolean }) {
  const body = useRef<Mesh>(null);
  const shell = useRef<Mesh>(null);
  const level = useRef(0);
  useFrame((state, dt) => {
    const live = Math.max(readVoiceInputLevel(), voiceOutputLevelRef.current ?? 0);
    // Attack fast, release slow — a syllable lands at once and fades gently.
    level.current += (live - level.current) * (live > level.current ? 0.5 : 0.08);
    const breath = reduced ? 0 : Math.sin(state.clock.elapsedTime * 1.6) * 0.025;
    const scale = 1 + level.current * 0.5 + breath;
    if (body.current) {
      body.current.scale.setScalar(scale);
      (body.current.material as MeshBasicMaterial).opacity = 0.16 + level.current * 0.45;
    }
    if (shell.current) {
      shell.current.scale.setScalar(1.25 + level.current * 0.35);
      if (!reduced) shell.current.rotation.y += dt * 0.15;
    }
  });
  return (
    <group raycast={() => undefined}>
      <mesh ref={body} raycast={() => undefined}>
        <sphereGeometry args={[CORE_RADIUS, 32, 24]} />
        <meshBasicMaterial
          color={CORE_HEX}
          transparent
          opacity={0.16}
          depthWrite={false}
          blending={AdditiveBlending}
          toneMapped={false}
        />
      </mesh>
      <mesh ref={shell} raycast={() => undefined}>
        <icosahedronGeometry args={[CORE_RADIUS, 1]} />
        <meshBasicMaterial color={CORE_HEX} wireframe transparent opacity={0.18} toneMapped={false} />
      </mesh>
    </group>
  );
}

/** A ring around the focused node, always facing the camera. */
function FocusRing({ pos, scale }: { pos: Vec3; scale: number }) {
  const ring = useRef<Mesh>(null);
  useFrame(({ camera, clock }) => {
    if (!ring.current) return;
    ring.current.quaternion.copy(camera.quaternion);
    ring.current.scale.setScalar(scale * (1 + Math.sin(clock.elapsedTime * 3) * 0.08));
  });
  return (
    <mesh ref={ring} position={pos} raycast={() => undefined}>
      <ringGeometry args={[NODE_RADIUS * 2.6, NODE_RADIUS * 3.2, 32]} />
      <meshBasicMaterial color="#ffffff" transparent opacity={0.85} depthWrite={false} toneMapped={false} />
    </mesh>
  );
}

/**
 * Eases the camera round the orb until the focused node faces it, and owns
 * the idle rotation: on when nothing has been touched or focused for a
 * while, off while easing. A drag cancels the easing — the hand wins.
 */
function CameraRig({
  target,
  controls,
  lastTouch,
  reduced,
}: {
  target: Vec3 | null;
  controls: React.RefObject<OrbitControlsImpl>;
  /** When the person last grabbed the orb (OrbitControls' `onStart`). */
  lastTouch: React.MutableRefObject<number>;
  reduced: boolean;
}) {
  const { camera, invalidate } = useThree();
  const easing = useRef(false);
  const focusedAt = useRef(0);
  const want = useMemo(() => new Vector3(), []);
  const dir = useMemo(() => new Vector3(), []);
  const targetKey = target ? target.join(",") : "";

  useEffect(() => {
    if (!target) {
      easing.current = false;
      return;
    }
    focusedAt.current = performance.now();
    want.set(target[0], target[1], target[2]).normalize();
    if (reduced) {
      // No travel under reduced motion: the camera is simply there.
      camera.position.copy(want).multiplyScalar(FOCUS_DISTANCE);
      controls.current?.update();
      invalidate();
      return;
    }
    easing.current = true;
    // `targetKey` stands for `target`: a new array with the same place is no new focus.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetKey, reduced]);

  useFrame((_, dt) => {
    const ctl = controls.current;
    if (!ctl) return;
    if (easing.current && lastTouch.current > focusedAt.current) easing.current = false;
    if (easing.current) {
      const distance = camera.position.length();
      const k = 1 - Math.exp(-dt * EASE_RATE);
      dir.copy(camera.position).normalize().lerp(want, k).normalize();
      camera.position.copy(dir).multiplyScalar(distance + (FOCUS_DISTANCE - distance) * k);
      ctl.update();
      if (dir.angleTo(want) < 0.004 && Math.abs(distance - FOCUS_DISTANCE) < 0.01) easing.current = false;
    }
    const quietFor = performance.now() - Math.max(lastTouch.current, focusedAt.current);
    ctl.autoRotate = !reduced && !easing.current && quietFor > IDLE_AFTER_MS;
  });
  return null;
}

/** A renderer that throws (no context after all, a driver refusing) degrades to the shell's text. */
class RenderBoundary extends Component<{ children: ReactNode; onFail: () => void }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch(error: Error) {
    console.warn("[memory orb] renderer unavailable", error);
    this.props.onFail();
  }
  render() {
    return this.state.failed ? null : this.props.children;
  }
}
