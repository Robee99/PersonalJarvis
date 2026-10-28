import { useEffect, useRef, useState } from "react";
import {
  AdditiveBlending,
  BackSide,
  BufferAttribute,
  BufferGeometry,
  CanvasTexture,
  Color,
  Float32BufferAttribute,
  Line,
  LineBasicMaterial,
  Mesh,
  PerspectiveCamera,
  Points,
  PointsMaterial,
  Scene,
  ShaderMaterial,
  SphereGeometry,
  SRGBColorSpace,
  type Texture,
  TextureLoader,
  Vector3,
  WebGLRenderer,
} from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { useWebglSurface } from "@/hooks/useWebglSurface";
import { latLonToVec, sunDirection } from "@/lib/spaceMath";
import { cn } from "@/lib/utils";

/**
 * The Earth seen from space, drawn with three.js.
 *
 * Day side from the Blue Marble mosaic, city lights from the Black Marble
 * mosaic on the night side, blended across the terminator by where the Sun
 * really is for the current UTC time (`sunDirection`). Oceans catch a little
 * sunlight; a cloud layer drawn from noise drifts slowly above; a thin
 * atmosphere glows at the rim. Stations sit at their real height above the
 * ground (the Earth's radius is 1), and the ISS's path for the next orbit is
 * a line.
 *
 * It is the heavy part of the Orbital screen, so the screen loads it on
 * demand. A missing texture leaves a plain ocean-blue sphere; a renderer that
 * cannot start calls `onFail` and the screen shows its flat globe instead.
 */
export interface SpacePoint {
  lat: number;
  lon: number;
  alt_km: number;
}

export interface SpaceEarthProps {
  iss: SpacePoint | null;
  stations: SpacePoint[];
  /** The ISS's path for the next orbit, in order. */
  track: SpacePoint[];
  /** Keep the camera over the ISS. */
  follow: boolean;
  /** Label drawn next to the ISS. */
  issLabel: string;
  /** What a screen reader hears. */
  label: string;
  onFail: (error: unknown) => void;
  className?: string;
}

const TEXTURES = {
  day: "/space/earth-day.jpg",
  night: "/space/earth-night.jpg",
  water: "/space/earth-water.jpg",
};

const EARTH_VERTEX = /* glsl */ `
  varying vec2 vUv;
  varying vec3 vNormal;
  varying vec3 vWorld;
  void main() {
    vUv = uv;
    vNormal = normalize(mat3(modelMatrix) * normal);
    vec4 world = modelMatrix * vec4(position, 1.0);
    vWorld = world.xyz;
    gl_Position = projectionMatrix * viewMatrix * world;
  }
`;

const EARTH_FRAGMENT = /* glsl */ `
  uniform sampler2D dayMap;
  uniform sampler2D nightMap;
  uniform sampler2D waterMap;
  uniform float hasMaps;
  uniform vec3 sunDir;
  varying vec2 vUv;
  varying vec3 vNormal;
  varying vec3 vWorld;
  void main() {
    vec3 n = normalize(vNormal);
    float light = dot(n, sunDir);
    float day = smoothstep(-0.12, 0.18, light);
    vec3 ocean = vec3(0.04, 0.16, 0.34);
    vec3 dayColor = mix(ocean, texture2D(dayMap, vUv).rgb, hasMaps);
    vec3 nightColor = texture2D(nightMap, vUv).rgb * hasMaps;
    float water = texture2D(waterMap, vUv).r * hasMaps;
    vec3 viewDir = normalize(cameraPosition - vWorld);
    vec3 halfway = normalize(sunDir + viewDir);
    float spec = pow(max(dot(n, halfway), 0.0), 40.0) * water * 0.55 * day;
    vec3 lit = dayColor * (0.08 + 0.95 * max(light, 0.0)) + vec3(1.0, 0.92, 0.75) * spec;
    vec3 lights = nightColor * vec3(1.0, 0.82, 0.55) * 1.35 * (1.0 - day);
    vec3 color = mix(lights + dayColor * 0.025, lit, day);
    gl_FragColor = vec4(color, 1.0);
    #include <colorspace_fragment>
  }
`;

const CLOUD_FRAGMENT = /* glsl */ `
  uniform vec3 sunDir;
  uniform float time;
  varying vec3 vNormal;
  varying vec3 vPos;
  float hash(vec3 p) { return fract(sin(dot(p, vec3(127.1, 311.7, 74.7))) * 43758.5453); }
  float noise(vec3 p) {
    vec3 i = floor(p);
    vec3 f = fract(p);
    f = f * f * (3.0 - 2.0 * f);
    return mix(
      mix(mix(hash(i), hash(i + vec3(1, 0, 0)), f.x), mix(hash(i + vec3(0, 1, 0)), hash(i + vec3(1, 1, 0)), f.x), f.y),
      mix(mix(hash(i + vec3(0, 0, 1)), hash(i + vec3(1, 0, 1)), f.x), mix(hash(i + vec3(0, 1, 1)), hash(i + vec3(1, 1, 1)), f.x), f.y),
      f.z);
  }
  float fbm(vec3 p) {
    float v = 0.0;
    float a = 0.5;
    for (int i = 0; i < 5; i++) { v += a * noise(p); p *= 2.07; a *= 0.5; }
    return v;
  }
  void main() {
    vec3 p = normalize(vPos);
    float c = fbm(p * 3.2 + vec3(time * 0.006, 0.0, time * 0.004));
    float band = 1.0 - abs(p.y) * 0.35;
    float alpha = smoothstep(0.47, 0.74, c * band) * 0.78;
    float light = dot(normalize(vNormal), sunDir);
    float day = smoothstep(-0.15, 0.25, light);
    gl_FragColor = vec4(vec3(0.92 + 0.08 * day) * (0.06 + 0.94 * day), alpha * (0.25 + 0.75 * day));
  }
`;

const CLOUD_VERTEX = /* glsl */ `
  varying vec3 vNormal;
  varying vec3 vPos;
  void main() {
    vPos = position;
    vNormal = normalize(mat3(modelMatrix) * normal);
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

const ATMOSPHERE_VERTEX = /* glsl */ `
  varying vec3 vNormal;
  varying vec3 vWorld;
  void main() {
    vNormal = normalize(mat3(modelMatrix) * normal);
    vec4 world = modelMatrix * vec4(position, 1.0);
    vWorld = world.xyz;
    gl_Position = projectionMatrix * viewMatrix * world;
  }
`;

const ATMOSPHERE_FRAGMENT = /* glsl */ `
  uniform vec3 sunDir;
  varying vec3 vNormal;
  varying vec3 vWorld;
  void main() {
    vec3 viewDir = normalize(cameraPosition - vWorld);
    float rim = pow(1.0 - abs(dot(normalize(vNormal), viewDir)), 3.0);
    float day = smoothstep(-0.35, 0.4, dot(normalize(vNormal), sunDir));
    vec3 color = mix(vec3(0.08, 0.2, 0.55), vec3(0.35, 0.65, 1.0), day);
    gl_FragColor = vec4(color, rim * (0.25 + 0.75 * day));
  }
`;

/** A starfield: points on a far sphere, a few brighter than the rest. */
function starField(count: number): BufferGeometry {
  const positions = new Float32Array(count * 3);
  const colors = new Float32Array(count * 3);
  let seed = 7;
  const rand = () => {
    seed = (seed * 16807) % 2147483647;
    return seed / 2147483647;
  };
  for (let i = 0; i < count; i += 1) {
    const u = rand() * 2 - 1;
    const a = rand() * Math.PI * 2;
    const r = 60 + rand() * 20;
    const s = Math.sqrt(1 - u * u);
    positions.set([r * s * Math.cos(a), r * u, r * s * Math.sin(a)], i * 3);
    const b = 0.35 + rand() ** 3 * 0.65;
    const warm = rand();
    colors.set([b * (0.85 + warm * 0.15), b * 0.9, b * (1 - warm * 0.15)], i * 3);
  }
  const geometry = new BufferGeometry();
  geometry.setAttribute("position", new BufferAttribute(positions, 3));
  geometry.setAttribute("color", new BufferAttribute(colors, 3));
  return geometry;
}

/** A soft round dot, so points draw as glows rather than squares. */
function dotTexture(): CanvasTexture {
  const size = 64;
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext("2d");
  if (ctx) {
    const g = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
    g.addColorStop(0, "rgba(255,255,255,1)");
    g.addColorStop(0.25, "rgba(255,255,255,0.9)");
    g.addColorStop(0.6, "rgba(255,255,255,0.25)");
    g.addColorStop(1, "rgba(255,255,255,0)");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, size, size);
  }
  return new CanvasTexture(canvas);
}

function toPositions(points: SpacePoint[]): Float32Array {
  const out = new Float32Array(points.length * 3);
  points.forEach((p, i) => out.set(latLonToVec(p.lat, p.lon, p.alt_km), i * 3));
  return out;
}

export default function SpaceEarth({ iss, stations, track, follow, issLabel, label, onFail, className }: SpaceEarthProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const labelRef = useRef<HTMLDivElement>(null);
  const { generation } = useWebglSurface(hostRef);
  const live = useRef({ iss, stations, track, follow });
  live.current = { iss, stations, track, follow };
  const dirty = useRef(true);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    dirty.current = true;
  }, [iss, stations, track]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const host = hostRef.current;
    if (!canvas || !host) return;
    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
    let renderer: WebGLRenderer;
    try {
      renderer = new WebGLRenderer({ canvas, antialias: true, alpha: false, powerPreference: "low-power" });
    } catch (error) {
      onFail(error);
      return;
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.75));
    renderer.setClearColor(new Color(0x01030a), 1);

    const scene = new Scene();
    const camera = new PerspectiveCamera(40, 1, 0.05, 200);
    const start = live.current.iss ?? { lat: 20, lon: 0, alt_km: 0 };
    camera.position.set(...latLonToVec(start.lat, start.lon, 0)).multiplyScalar(3.4);

    const controls = new OrbitControls(camera, canvas);
    controls.enableDamping = !reduced;
    controls.dampingFactor = 0.08;
    controls.enablePan = false;
    controls.minDistance = 1.25;
    controls.maxDistance = 9;
    controls.rotateSpeed = 0.45;
    controls.zoomSpeed = 0.7;
    controls.autoRotate = !reduced;
    controls.autoRotateSpeed = 0.18;

    const sun = new Vector3(...sunDirection(Date.now()));
    const disposables: { dispose: () => void }[] = [];
    const own = <T extends { dispose: () => void }>(x: T): T => {
      disposables.push(x);
      return x;
    };

    const loader = new TextureLoader();
    const blank = { value: null as Texture | null };
    const earthUniforms = {
      dayMap: { ...blank },
      nightMap: { ...blank },
      waterMap: { ...blank },
      hasMaps: { value: 0 },
      sunDir: { value: sun },
    };
    let loaded = 0;
    (["day", "night", "water"] as const).forEach((key) => {
      loader.load(
        TEXTURES[key],
        (tex) => {
          if (key !== "water") tex.colorSpace = SRGBColorSpace;
          tex.anisotropy = Math.min(8, renderer.capabilities.getMaxAnisotropy());
          own(tex);
          earthUniforms[`${key}Map`].value = tex;
          loaded += 1;
          if (loaded === 3) earthUniforms.hasMaps.value = 1;
          dirty.current = true;
        },
        undefined,
        (error) => {
          // The sphere stays a plain ocean blue; the rest of the scene is unaffected.
          console.info(`Space view texture ${TEXTURES[key]} did not load.`, error);
        },
      );
    });

    const earthGeo = own(new SphereGeometry(1, 128, 64));
    const earth = new Mesh(
      earthGeo,
      own(new ShaderMaterial({ uniforms: earthUniforms, vertexShader: EARTH_VERTEX, fragmentShader: EARTH_FRAGMENT })),
    );
    scene.add(earth);

    const cloudUniforms = { sunDir: { value: sun }, time: { value: 0 } };
    const clouds = new Mesh(
      own(new SphereGeometry(1.008, 96, 48)),
      own(
        new ShaderMaterial({
          uniforms: cloudUniforms,
          vertexShader: CLOUD_VERTEX,
          fragmentShader: CLOUD_FRAGMENT,
          transparent: true,
          depthWrite: false,
        }),
      ),
    );
    scene.add(clouds);

    const atmosphere = new Mesh(
      own(new SphereGeometry(1.045, 96, 48)),
      own(
        new ShaderMaterial({
          uniforms: { sunDir: { value: sun } },
          vertexShader: ATMOSPHERE_VERTEX,
          fragmentShader: ATMOSPHERE_FRAGMENT,
          side: BackSide,
          transparent: true,
          blending: AdditiveBlending,
          depthWrite: false,
        }),
      ),
    );
    scene.add(atmosphere);

    const dot = own(dotTexture());
    const stars = new Points(
      own(starField(2600)),
      own(new PointsMaterial({ map: dot, size: 0.3, vertexColors: true, transparent: true, sizeAttenuation: true, depthWrite: false })),
    );
    scene.add(stars);

    const stationGeo = own(new BufferGeometry());
    stationGeo.setAttribute("position", new Float32BufferAttribute([], 3));
    const stationPoints = new Points(
      stationGeo,
      own(new PointsMaterial({ map: dot, color: 0x5fd8ff, size: 0.05, sizeAttenuation: true, transparent: true, opacity: 0.95, depthWrite: false })),
    );
    scene.add(stationPoints);

    const issGeo = own(new BufferGeometry());
    issGeo.setAttribute("position", new Float32BufferAttribute([0, 0, 0], 3));
    const issPoint = new Points(
      issGeo,
      own(new PointsMaterial({ map: dot, color: 0xffb060, size: 0.07, sizeAttenuation: true, transparent: true, depthWrite: false })),
    );
    const issHalo = new Points(
      issGeo,
      own(new PointsMaterial({ map: dot, color: 0xff9a3c, size: 0.22, sizeAttenuation: true, transparent: true, opacity: 0.25, blending: AdditiveBlending, depthWrite: false })),
    );
    scene.add(issPoint, issHalo);

    const trackGeo = own(new BufferGeometry());
    trackGeo.setAttribute("position", new Float32BufferAttribute([], 3));
    const trackLine = new Line(trackGeo, own(new LineBasicMaterial({ color: 0xff9a3c, transparent: true, opacity: 0.55 })));
    scene.add(trackLine);

    const applyData = () => {
      const { iss: now, stations: others, track: path } = live.current;
      stationGeo.setAttribute("position", new BufferAttribute(toPositions(others), 3));
      trackGeo.setAttribute("position", new BufferAttribute(toPositions(path), 3));
      issPoint.visible = issHalo.visible = now !== null;
      if (now) issGeo.setAttribute("position", new BufferAttribute(new Float32Array(latLonToVec(now.lat, now.lon, now.alt_km)), 3));
      dirty.current = false;
    };

    const resize = () => {
      const w = host.clientWidth || 1;
      const h = host.clientHeight || 1;
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    };
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(host);

    const issWorld = new Vector3();
    const goal = new Vector3();
    let raf = 0;
    let lastSun = 0;
    const startedAt = performance.now();
    const tick = () => {
      raf = window.requestAnimationFrame(tick);
      if (document.visibilityState !== "visible") return;
      const nowMs = Date.now();
      if (nowMs - lastSun > 30_000) {
        sun.set(...sunDirection(nowMs));
        lastSun = nowMs;
      }
      if (dirty.current) applyData();
      const { iss: here, follow: following } = live.current;
      controls.autoRotate = !reduced && !following;
      if (following && here) {
        issWorld.set(...latLonToVec(here.lat, here.lon, here.alt_km));
        goal.copy(issWorld).normalize().multiplyScalar(camera.position.length());
        camera.position.lerp(goal, reduced ? 1 : 0.05);
      }
      if (!reduced) cloudUniforms.time.value = (performance.now() - startedAt) / 1000;
      controls.update();
      renderer.render(scene, camera);
      const tag = labelRef.current;
      if (tag) {
        if (here) {
          issWorld.set(...latLonToVec(here.lat, here.lon, here.alt_km));
          const facing = issWorld.dot(camera.position) > 1;
          const p = issWorld.clone().project(camera);
          tag.style.display = facing ? "block" : "none";
          tag.style.transform = `translate(${((p.x + 1) / 2) * host.clientWidth + 10}px, ${((1 - p.y) / 2) * host.clientHeight - 8}px)`;
        } else {
          tag.style.display = "none";
        }
      }
    };
    raf = window.requestAnimationFrame(tick);
    setReady(true);

    return () => {
      window.cancelAnimationFrame(raf);
      observer.disconnect();
      controls.dispose();
      disposables.forEach((d) => d.dispose());
      renderer.dispose();
      // The context itself is handed back by useWebglSurface, one task later:
      // StrictMode's replayed effect builds the next renderer on this same
      // canvas, and a context lost here would leave it unable to start.
    };
  }, [generation]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div
      ref={hostRef}
      className={cn("relative overflow-hidden cursor-grab active:cursor-grabbing touch-none", className)}
      role="img"
      aria-label={label}
      data-testid="stark-space-earth"
      data-ready={ready ? "true" : "false"}
    >
      <canvas key={generation} ref={canvasRef} className="block h-full w-full" />
      <div
        ref={labelRef}
        className="pointer-events-none absolute left-0 top-0 hidden whitespace-nowrap font-mono text-[10px] font-semibold uppercase tracking-[0.18em] text-[hsl(32_100%_62%)] [text-shadow:0_0_6px_rgba(0,0,0,0.9)]"
        aria-hidden
      >
        {issLabel}
      </div>
    </div>
  );
}
