/**
 * Real hand control for the Orb, on the same tracking the Hands view runs.
 *
 * Why the Orb had none: the Hands view is the upstream holo page in an
 * iframe (components/wiki/HoloDeck.tsx → /holo/), and every gesture it reads
 * stays inside that page; it posts its state to /api/holo/state, which is
 * acknowledged and dropped. The Orb is a separate canvas that only ever
 * listened to the mouse, so no hand could reach it.
 *
 * This hook loads the very files that page uses (MediaPipe Tasks Vision and
 * the hand model, downloaded once by POST /api/holo/install and served from
 * /holo/vendor/...), runs the landmarker on a local camera stream, and feeds
 * each frame to the pure gesture engine (`gestures.ts`), whose actions go to
 * the Orb's own handlers. Frames are read in this page and never sent
 * anywhere; nothing leaves the browser but the vendor-file GETs.
 *
 * Every failure has a status the toggle can explain (no camera, camera
 * refused or busy, tracking files missing or failing to load), and the mouse,
 * keyboard and touch keep working whatever the state.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import {
  GestureEngine,
  dedupeHands,
  type GestureAction,
  type GestureId,
  type HandCursor,
  type HandLandmarks,
  type LiveGesture,
} from "./gestures";

export const VENDOR = "/holo/vendor";

export type HandStatus =
  | "off"
  | "checking"
  | "files-missing"
  | "installing"
  | "install-failed"
  | "loading"
  | "tracking-failed"
  | "camera"
  | "camera-denied"
  | "no-camera"
  | "camera-busy"
  | "camera-failed"
  | "unsupported"
  | "live";

/** Statuses that are a problem the person should read about. */
export const HAND_PROBLEMS: HandStatus[] = [
  "files-missing",
  "install-failed",
  "tracking-failed",
  "camera-denied",
  "no-camera",
  "camera-busy",
  "camera-failed",
  "unsupported",
];

export interface Detector {
  detect(video: HTMLVideoElement, now: number): HandLandmarks[];
  close(): void;
}

/** Everything that touches the machine, injectable so tests need no camera. */
export interface HandDeps {
  supported(): boolean;
  checkFiles(): Promise<boolean>;
  installFiles(): Promise<void>;
  createDetector(): Promise<Detector>;
  openCamera(): Promise<MediaStream>;
}

export interface HandController {
  onAction(action: GestureAction): void;
}

interface MediaPipeVision {
  FilesetResolver: { forVisionTasks(path: string): Promise<unknown> };
  HandLandmarker: {
    createFromOptions(
      files: unknown,
      options: Record<string, unknown>,
    ): Promise<{
      detectForVideo(
        video: HTMLVideoElement,
        now: number,
      ): {
        landmarks?: HandLandmarks[];
        handedness?: { score: number }[][];
        handednesses?: { score: number }[][];
      };
      close(): void;
    }>;
  };
}

async function createVendorDetector(): Promise<Detector> {
  // A runtime URL, not a module Vite should bundle: the files live in the
  // user's data directory and are served by holo_routes.py.
  const mp = (await import(/* @vite-ignore */ `${VENDOR}/vision_bundle.mjs`)) as MediaPipeVision;
  const files = await mp.FilesetResolver.forVisionTasks(`${VENDOR}/wasm`);
  // Confidence floors as in the Hands view: a face or a shirt fold scoring
  // .5-.6 as a "hand" is exactly the phantom cursor it was tuned against.
  const options = (delegate: "GPU" | "CPU") => ({
    baseOptions: { modelAssetPath: `${VENDOR}/hand_landmarker.task`, delegate },
    runningMode: "VIDEO",
    numHands: 2,
    minHandDetectionConfidence: 0.6,
    minHandPresenceConfidence: 0.6,
    minTrackingConfidence: 0.6,
  });
  let landmarker: Awaited<ReturnType<MediaPipeVision["HandLandmarker"]["createFromOptions"]>>;
  try {
    landmarker = await mp.HandLandmarker.createFromOptions(files, options("GPU"));
  } catch (error) {
    // No WebGL2 (or a blocked GPU) is common in VMs and remote desktops; the
    // CPU delegate is slower but tracks the same.
    console.warn("[orb hands] GPU delegate failed, using CPU", error);
    landmarker = await mp.HandLandmarker.createFromOptions(files, options("CPU"));
  }
  return {
    detect(video, now) {
      const res = landmarker.detectForVideo(video, now);
      const scores = res.handedness ?? res.handednesses ?? [];
      // handedness score is left-vs-right confidence, not detection quality;
      // .5 is only a sanity floor (detection quality is enforced above).
      const hands = (res.landmarks ?? []).filter((_, i) => (scores[i]?.[0]?.score ?? 1) > 0.5);
      return dedupeHands(hands);
    },
    close() {
      landmarker.close();
    },
  };
}

export const browserHandDeps: HandDeps = {
  supported: () =>
    typeof navigator !== "undefined" &&
    !!navigator.mediaDevices &&
    typeof navigator.mediaDevices.getUserMedia === "function",
  async checkFiles() {
    const res = await fetch("/api/holo/install");
    if (!res.ok) return false;
    const body = (await res.json()) as { ready?: boolean };
    return !!body.ready;
  },
  async installFiles() {
    const res = await fetch("/api/holo/install", { method: "POST" });
    if (!res.ok) {
      const body = (await res.json().catch(() => ({}))) as { error?: string };
      throw new Error(body.error ?? `HTTP ${res.status}`);
    }
  },
  createDetector: createVendorDetector,
  openCamera: () =>
    navigator.mediaDevices.getUserMedia({
      video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: "user" },
      audio: false,
    }),
};

export function cameraStatus(error: unknown): HandStatus {
  const name = (error as { name?: string } | null)?.name ?? "";
  if (name === "NotAllowedError" || name === "SecurityError") return "camera-denied";
  if (name === "NotFoundError" || name === "OverconstrainedError") return "no-camera";
  if (name === "NotReadableError" || name === "AbortError") return "camera-busy";
  return "camera-failed";
}

const message = (error: unknown) => (error instanceof Error ? error.message : String(error));

/** A gesture that just did something, for the indicator to name. */
export interface FiredGesture {
  id: GestureId;
  step?: 1 | -1;
  /** Counts up with every firing, so the same gesture twice still re-renders. */
  seq: number;
}

/** The action a gesture fired, or null for continuous ones (hover, pan, zoom). */
export function firedGesture(action: GestureAction): Omit<FiredGesture, "seq"> | null {
  switch (action.type) {
    case "select":
      return { id: "select" };
    case "open":
      return { id: "open" };
    case "confirm":
      return { id: "confirm" };
    case "section":
      return { id: "swipe", step: action.step };
    case "reset":
      return { id: "reset" };
    case "fling":
      return { id: "flick" };
    case "pull":
      return { id: "pull" };
    case "push":
      return { id: "push" };
    default:
      return null;
  }
}

const sameGesture = (a: LiveGesture | null, b: LiveGesture | null) =>
  a === b ||
  (!!a && !!b && a.id === b.id && Math.round((a.progress ?? -1) * 20) === Math.round((b.progress ?? -1) * 20));

export interface HandTracking {
  status: HandStatus;
  detail: string;
  cursors: HandCursor[];
  /** The gesture the engine recognises right now. */
  gesture: LiveGesture | null;
  /** The last gesture that fired an action. */
  fired: FiredGesture | null;
  stream: MediaStream | null;
  /** Start tracking; `install` also downloads missing tracking files first. */
  start(options?: { install?: boolean }): void;
  stop(): void;
}

export function useHandTracking(controller: HandController, deps: HandDeps = browserHandDeps): HandTracking {
  const [status, setStatus] = useState<HandStatus>("off");
  const [detail, setDetail] = useState("");
  const [cursors, setCursors] = useState<HandCursor[]>([]);
  const [gesture, setGesture] = useState<LiveGesture | null>(null);
  const [fired, setFired] = useState<FiredGesture | null>(null);
  const firedSeq = useRef(0);
  const [stream, setStream] = useState<MediaStream | null>(null);
  const controllerRef = useRef(controller);
  controllerRef.current = controller;
  // Each start gets a generation; anything finishing for an older one is
  // discarded, so a quick off/on never leaves a second camera running.
  const generation = useRef(0);
  const live = useRef<{ detector?: Detector; media?: MediaStream; video?: HTMLVideoElement; raf?: number }>({});

  const teardown = useCallback(() => {
    const cur = live.current;
    if (cur.raf !== undefined) cancelAnimationFrame(cur.raf);
    cur.media?.getTracks().forEach((track) => track.stop());
    if (cur.video) cur.video.srcObject = null;
    cur.detector?.close();
    live.current = {};
  }, []);

  const stop = useCallback(() => {
    generation.current += 1;
    teardown();
    setCursors([]);
    setGesture(null);
    setFired(null);
    setStream(null);
    setDetail("");
    setStatus("off");
  }, [teardown]);

  const start = useCallback(
    (options?: { install?: boolean }) => {
      const gen = ++generation.current;
      teardown();
      const stale = () => gen !== generation.current;
      const fail = (next: HandStatus, why = "") => {
        if (stale()) return;
        teardown();
        setDetail(why);
        setStatus(next);
      };
      void (async () => {
        setDetail("");
        setGesture(null);
        setFired(null);
        if (!deps.supported()) return fail("unsupported");
        setStatus("checking");
        let ready = false;
        try {
          ready = await deps.checkFiles();
        } catch (error) {
          return fail("files-missing", message(error));
        }
        if (stale()) return;
        if (!ready) {
          if (!options?.install) return fail("files-missing");
          setStatus("installing");
          try {
            await deps.installFiles();
          } catch (error) {
            return fail("install-failed", message(error));
          }
          if (stale()) return;
        }
        setStatus("loading");
        let detector: Detector;
        try {
          detector = await deps.createDetector();
        } catch (error) {
          return fail("tracking-failed", message(error));
        }
        if (stale()) return detector.close();
        live.current.detector = detector;
        setStatus("camera");
        let media: MediaStream;
        try {
          media = await deps.openCamera();
        } catch (error) {
          return fail(cameraStatus(error), message(error));
        }
        if (stale()) {
          media.getTracks().forEach((track) => track.stop());
          return;
        }
        live.current.media = media;
        const video = document.createElement("video");
        video.muted = true;
        video.playsInline = true;
        video.srcObject = media;
        live.current.video = video;
        try {
          await video.play();
        } catch (error) {
          return fail("camera-failed", message(error));
        }
        if (stale()) return;
        setStream(media);
        setStatus("live");
        const engine = new GestureEngine();
        let lastTime = -1;
        const tick = () => {
          if (stale()) return;
          if (video.readyState >= 2 && video.currentTime !== lastTime) {
            lastTime = video.currentTime;
            const now = performance.now();
            const frame = engine.update(detector.detect(video, now), now);
            let last: Omit<FiredGesture, "seq"> | null = null;
            for (const action of frame.actions) {
              controllerRef.current.onAction(action);
              last = firedGesture(action) ?? last;
            }
            if (last) {
              firedSeq.current += 1;
              setFired({ ...last, seq: firedSeq.current });
            }
            setGesture((prev) => (sameGesture(prev, frame.gesture) ? prev : frame.gesture));
            setCursors((prev) => (prev.length === 0 && frame.cursors.length === 0 ? prev : frame.cursors));
          }
          live.current.raf = requestAnimationFrame(tick);
        };
        live.current.raf = requestAnimationFrame(tick);
      })();
    },
    [deps, teardown],
  );

  // The camera goes off with the view.
  useEffect(
    () => () => {
      generation.current += 1;
      teardown();
    },
    [teardown],
  );

  return { status, detail, cursors, gesture, fired, stream, start, stop };
}
