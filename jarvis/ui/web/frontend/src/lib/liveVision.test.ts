import { describe, expect, it, vi } from "vitest";
import { LiveVisionController, ScreenWatchGate, PostureNudgeGate } from "./liveVision";

function stream(surface = "monitor") {
  const track = { stop: vi.fn(), addEventListener: vi.fn(),
    getSettings: () => ({ displaySurface: surface }) };
  return { track, getTracks: () => [track], getVideoTracks: () => [track] };
}
function setup() {
  const camera = stream(), screen = stream();
  const media = { getUserMedia: vi.fn(async (_constraints: MediaStreamConstraints) => camera),
    getDisplayMedia: vi.fn(async (_constraints: DisplayMediaStreamOptions) => screen) };
  let next = 0;
  const controller = new LiveVisionController({
    media: media as unknown as MediaDevices,
    makeVideo: () => ({ play: async () => {}, pause() {}, srcObject: null }) as unknown as HTMLVideoElement,
    capture: (_video, source) => ({ source, mime: "image/jpeg", data_b64: `fresh-${++next}`, captured_at_ms: Date.now() }),
  });
  return { controller, camera, screen, media };
}

describe("independent visual sources", () => {
  it("requests video only and stopping the camera leaves the screen live", async () => {
    const { controller: c, camera, screen, media } = setup();
    await c.start("webcam"); await c.start("screen");
    expect(media.getUserMedia.mock.calls[0][0]).toMatchObject({ audio: false });
    expect(media.getDisplayMedia.mock.calls[0][0]).toMatchObject({ audio: false });
    c.stop("webcam");
    expect(camera.track.stop).toHaveBeenCalledOnce();
    expect(screen.track.stop).not.toHaveBeenCalled();
    expect(c.state.screen).toBe("on");
    c.dispose();
  });
  it("captures at question time and routes camera and display questions separately", async () => {
    const { controller: c } = setup();
    await c.start("webcam"); await c.start("screen");
    const first = c.forQuestion("look at my screen");
    const second = c.forQuestion("look at my screen");
    expect(first[0].source).toBe("screen");
    expect(first[0].data_b64).not.toBe(second[0].data_b64);
    expect(c.forQuestion("look at me").map(f => f.source)).toEqual(["webcam"]);
    expect(c.forQuestion("look at both").map(f => f.source)).toEqual(["screen", "webcam"]);
    c.stop("screen");
    expect(() => c.forQuestion("look at my screen")).toThrow(/screen/i);
    c.dispose();
  });
  it("stops a permission result which arrives after the source was switched off", async () => {
    const { controller: c, media } = setup();
    const late = stream(); let resolve!: (value: unknown) => void;
    media.getUserMedia.mockImplementationOnce(() => new Promise(r => { resolve = r; }) as never);
    const starting = c.start("webcam");
    c.stop("webcam"); resolve(late); await starting;
    expect(late.track.stop).toHaveBeenCalledOnce();
    expect(c.state.webcam).toBe("off");
  });
  it("an ended stream cannot provide a cached frame", async () => {
    const { controller: c, screen } = setup(); await c.start("screen");
    const ended = screen.track.addEventListener.mock.calls.find(row => row[0] === "ended")![1];
    ended();
    expect(() => c.capture("screen")).toThrow(/screen/i);
  });
  it("never enables whole-screen watch for a tab share", async () => {
    const { controller: c, media } = setup();
    media.getDisplayMedia.mockResolvedValueOnce(stream("browser"));
    await c.start("screen");
    expect(() => c.enableWatch()).toThrow(/entire screen/i);
    expect(c.state.screen).toBe("on");
    c.dispose();
  });
});

describe("local screen watch", () => {
  it("waits 60 seconds, resets on change and observes a three-minute cooldown", () => {
    const watch = new ScreenWatchGate();
    const a = new Uint8Array(128).fill(10), b = new Uint8Array(128).fill(240);
    expect(watch.sample(a, 0)).toBe(false);
    expect(watch.sample(a, 59_999)).toBe(false);
    expect(watch.sample(b, 60_000)).toBe(false);
    expect(watch.sample(b, 119_999)).toBe(false);
    expect(watch.sample(b, 120_000)).toBe(true);
    watch.accepted(120_000);
    expect(watch.sample(b, 299_999)).toBe(false);
    expect(watch.sample(b, 300_000)).toBe(true);
  });
  it("relief suppresses nudges without stopping either visual source", async () => {
    const { controller: c } = setup(); await c.start("webcam"); await c.start("screen");
    expect(c.relieve("give me a minute", 1000)).toBe(true);
    expect(c.quietUntil).toBe(181_000);
    expect(c.state.webcam).toBe("on"); expect(c.state.screen).toBe("on");
    c.dispose();
  });
});

it("requires sustained posture, longer absence grace, and a posture cooldown", () => {
  const gate = new PostureNudgeGate();
  expect(gate.sample({ present: true, headDown: true, slouched: false }, 0)).toBe(null);
  expect(gate.sample({ present: true, headDown: true, slouched: false }, 699)).toBe(null);
  expect(gate.sample({ present: true, headDown: true, slouched: false }, 700)).toBe("head-down");
  gate.accepted(700);
  expect(gate.sample({ present: false, headDown: false, slouched: false }, 1000)).toBe(null);
  expect(gate.sample({ present: false, headDown: false, slouched: false }, 13_000)).toBe(null);
  expect(gate.sample({ present: false, headDown: false, slouched: false }, 30_700)).toBe("absent");
});
