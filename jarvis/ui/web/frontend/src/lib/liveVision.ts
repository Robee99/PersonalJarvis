/** Independent video sources and local-only watch decisions. No microphone API. */
export type VisionSource = "screen" | "webcam";
export interface LiveVisionFrame {
  source: VisionSource;
  mime: "image/jpeg";
  data_b64: string;
  captured_at_ms: number;
}
export const WATCH_INTERVAL_MS = 5_000;
export const STILLNESS_MS = 60_000;
export const WATCH_COOLDOWN_MS = 180_000;
export const POSTURE_SUSTAIN_MS = 700;
export const POSTURE_COOLDOWN_MS = 30_000;
export const ABSENCE_GRACE_MS = 12_000;
export const RELIEF_MS = 180_000;

export class ScreenWatchGate {
  private previous?: Uint8Array;
  private stillSince = 0;
  private nextAllowed = 0;
  sample(pixels: Uint8Array, now: number): boolean {
    const old = this.previous;
    const difference = old && old.length === pixels.length
      ? pixels.reduce((sum, value, i) => sum + Math.abs(value-old[i]), 0)/pixels.length : Infinity;
    if (difference > 8) this.stillSince = now;
    this.previous = pixels.slice();
    return now-this.stillSince >= STILLNESS_MS && now >= this.nextAllowed;
  }
  accepted(now: number): void { this.nextAllowed = now+WATCH_COOLDOWN_MS; }
}

export interface PostureSignals { present: boolean; headDown: boolean; slouched: boolean }
export class PostureNudgeGate {
  private reason: "absent" | "head-down" | "slouched" | null = null;
  private since = 0;
  private nextAllowed = 0;
  sample(signals: PostureSignals, now: number): typeof this.reason {
    const reason = !signals.present ? "absent" : signals.headDown ? "head-down" : signals.slouched ? "slouched" : null;
    if (reason !== this.reason) { this.reason = reason; this.since = now; }
    const sustain = reason === "absent" ? ABSENCE_GRACE_MS : POSTURE_SUSTAIN_MS;
    return reason && now-this.since >= sustain && now >= this.nextAllowed ? reason : null;
  }
  accepted(now: number): void { this.nextAllowed = now+POSTURE_COOLDOWN_MS; }
}

export function jpegFrame(video: HTMLVideoElement, source: VisionSource): LiveVisionFrame {
  if (!video.videoWidth || !video.videoHeight || video.readyState < 2) {
    throw new Error(`${source} has no current frame yet`);
  }
  const scale = Math.min(1, 1280/Math.max(video.videoWidth, video.videoHeight));
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(video.videoWidth*scale));
  canvas.height = Math.max(1, Math.round(video.videoHeight*scale));
  canvas.getContext("2d")!.drawImage(video, 0, 0, canvas.width, canvas.height);
  const data = canvas.toDataURL("image/jpeg", .85).split(",")[1];
  if (!data || data.length > 1_000_000) throw new Error("The live frame is too large to send");
  return { source, mime: "image/jpeg", data_b64: data, captured_at_ms: Date.now() };
}

export function thumbnail(video: HTMLVideoElement): Uint8Array {
  if (!video.videoWidth || video.readyState < 2) throw new Error("Screen has no current frame yet");
  const canvas = document.createElement("canvas"); canvas.width = 48; canvas.height = 27;
  const ctx = canvas.getContext("2d", { willReadFrequently: true })!;
  ctx.drawImage(video, 0, 0, 48, 27);
  return new Uint8Array(ctx.getImageData(0, 0, 48, 27).data);
}

interface Options {
  media?: MediaDevices;
  makeVideo?: () => HTMLVideoElement;
  capture?: typeof jpegFrame;
  changed?: () => void;
}

export class LiveVisionController {
  readonly state: Record<VisionSource, "off" | "starting" | "on"> = { screen: "off", webcam: "off" };
  readonly videos: Partial<Record<VisionSource, HTMLVideoElement>> = {};
  private streams: Partial<Record<VisionSource, MediaStream>> = {};
  private epochs = { screen: 0, webcam: 0 };
  private readonly options: Options;
  watching = false;
  watch = new ScreenWatchGate();
  quietUntil = 0;
  constructor(options: Options = {}) { this.options = options; }
  private changed(): void {
    this.options.changed?.();
    if (typeof window !== "undefined") window.dispatchEvent(new Event("jarvis-live-vision"));
  }
  async start(source: VisionSource): Promise<void> {
    this.stop(source);
    const epoch = ++this.epochs[source];
    this.state[source] = "starting"; this.changed();
    const media = this.options.media ?? navigator.mediaDevices;
    let stream: MediaStream | undefined;
    try {
      if (source === "webcam") {
        if (!media?.getUserMedia) throw new Error("Webcam access is unavailable in this window");
        stream = await media.getUserMedia({ video: { facingMode: "user" }, audio: false });
      } else {
        if (!media?.getDisplayMedia) throw new Error("Screen sharing is unavailable in this window");
        stream = await media.getDisplayMedia({ video: { displaySurface: "monitor" }, audio: false });
      }
      if (this.epochs[source] !== epoch) { stream.getTracks().forEach(t => t.stop()); return; }
      const video = this.options.makeVideo?.() ?? document.createElement("video");
      video.muted = true; video.playsInline = true; video.srcObject = stream;
      this.streams[source] = stream; this.videos[source] = video;
      stream.getVideoTracks().forEach(track => track.addEventListener("ended", () => {
        if (this.epochs[source] === epoch) this.stop(source);
      }));
      await video.play();
      if (this.epochs[source] !== epoch) return;
      this.state[source] = "on"; this.changed();
    } catch (error) {
      stream?.getTracks().forEach(t => t.stop());
      if (this.epochs[source] === epoch) this.stop(source);
      throw error;
    }
  }
  stop(source: VisionSource): void {
    this.epochs[source]++;
    this.streams[source]?.getTracks().forEach(track => track.stop());
    const video = this.videos[source];
    if (video) { video.pause(); video.srcObject = null; }
    delete this.streams[source]; delete this.videos[source];
    this.state[source] = "off";
    if (source === "screen") { this.watching = false; this.watch = new ScreenWatchGate(); }
    this.changed();
  }
  capture(source: VisionSource): LiveVisionFrame {
    const video = this.videos[source];
    if (this.state[source] !== "on" || !video) throw new Error(`${source} is not sharing`);
    return (this.options.capture ?? jpegFrame)(video, source);
  }
  forQuestion(text: string): LiveVisionFrame[] {
    const camera = /\b(look at me|see me|my (?:shirt|face|posture)|webcam|camera)\b/i.test(text);
    const screen = /\b(screen|desktop|window)\b/i.test(text);
    const both = /\bboth\b/i.test(text) || camera && screen;
    if (this.state.screen === "off" && this.state.webcam === "off") return [];
    const sources: VisionSource[] = both ? ["screen", "webcam"] : camera ? ["webcam"]
      : screen || this.state.screen === "on" ? ["screen"] : [];
    return sources.map(source => this.capture(source));
  }
  enableWatch(): void {
    const track = this.streams.screen?.getVideoTracks()[0];
    if (!track || track.getSettings().displaySurface !== "monitor") {
      throw new Error("Watch requires Entire Screen. Stop this share and select Entire Screen in the picker.");
    }
    this.watching = true; this.watch = new ScreenWatchGate(); this.changed();
  }
  disableWatch(): void { this.watching = false; this.changed(); }
  relieve(text: string, now = Date.now()): boolean {
    if (!/\b(give me a minute|i need to do something important|pause (?:the )?(?:eyes|watch|nudges))\b/i.test(text)) return false;
    this.quietUntil = now+RELIEF_MS; this.changed(); return true;
  }
  dispose(): void { this.stop("screen"); this.stop("webcam"); }
}

let shared: LiveVisionController | undefined;
export function getLiveVision(): LiveVisionController { return shared ??= new LiveVisionController(); }
