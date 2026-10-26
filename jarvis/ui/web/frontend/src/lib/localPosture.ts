/** Local face/pose models, downloaded once on first use. Frames never leave the browser. */
import type { PostureSignals } from "./liveVision";

type Landmark = { x: number; y: number; z: number; visibility?: number };
type Detector = { detectForVideo: (video: HTMLVideoElement, now: number) => {
  faceLandmarks?: Landmark[][]; landmarks?: Landmark[][];
  facialTransformationMatrixes?: { data: number[] }[];
}; close: () => void };

export class LocalPostureTracker {
  private constructor(private face: Detector, private pose: Detector) {}
  static async create(): Promise<LocalPostureTracker> {
    const check = async () => {
      const response = await fetch("/api/live-vision/capabilities", { signal: AbortSignal.timeout(5000) });
      if (!response.ok) throw new Error("Local posture capability could not be checked");
      return await response.json() as { tracking_ready: boolean; detail: string };
    };
    let status = await check();
    if (!status.tracking_ready) {
      // First use: fetch the pinned face/pose models (about 10 MB) once, then look again.
      const install = await fetch("/api/holo/install", { method: "POST" });
      if (!install.ok) {
        const body = await install.json().catch(() => ({})) as { error?: string };
        throw new Error(body.error || status.detail);
      }
      status = await check();
      if (!status.tracking_ready) throw new Error(status.detail);
    }
    const url = "/holo/vendor/vision_bundle.mjs";
    const sdk = await import(/* @vite-ignore */ url);
    const files = await sdk.FilesetResolver.forVisionTasks("/holo/vendor/wasm");
    const face = await sdk.FaceLandmarker.createFromOptions(files, {
      baseOptions: { modelAssetPath: "/api/live-vision/tracking/face_landmarker.task" },
      runningMode: "VIDEO", numFaces: 1, outputFacialTransformationMatrixes: true,
    }) as Detector;
    try {
      const pose = await sdk.PoseLandmarker.createFromOptions(files, {
        baseOptions: { modelAssetPath: "/api/live-vision/tracking/pose_landmarker_lite.task" },
        runningMode: "VIDEO", numPoses: 1,
      }) as Detector;
      return new LocalPostureTracker(face, pose);
    } catch (error) { face.close(); throw error; }
  }
  sample(video: HTMLVideoElement, now: number): PostureSignals {
    const face = this.face.detectForVideo(video, now);
    const pose = this.pose.detectForVideo(video, now).landmarks?.[0];
    const rotation = face.facialTransformationMatrixes?.[0]?.data;
    const pitch = rotation ? Math.atan2(rotation[6], rotation[10])*180/Math.PI : 0;
    const visible = (points: number[]) => !!pose && points.every(i => (pose[i]?.visibility ?? 0) > .6);
    const shoulders = pose ? (pose[11].y+pose[12].y)/2 : 0;
    const ears = pose ? (pose[7].y+pose[8].y)/2 : 0;
    const hips = pose ? (pose[23].y+pose[24].y)/2 : 0;
    return { present: !!face.faceLandmarks?.length,
      headDown: !!rotation && pitch > 22,
      slouched: visible([7, 8, 11, 12, 23, 24]) && hips > shoulders && (shoulders-ears)/(hips-shoulders) < .22 };
  }
  close(): void { this.face.close(); this.pose.close(); }
}
