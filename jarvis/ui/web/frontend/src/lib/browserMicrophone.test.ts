import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MicrophoneSelectionError, openBrowserMicrophone, selectedBrowserMicrophone } from "./browserMicrophone";

function input(deviceId: string, label: string): MediaDeviceInfo {
  return { deviceId, label, kind: "audioinput", groupId: "", toJSON: () => ({}) };
}

const headsetName = "Headset (boAt Rockerz 255 Pro+)";
const laptop = input("laptop-id", "Microphone Array (Realtek(R) Audio)");
const headset = input("headset-id", headsetName);

describe("browser microphone selection", () => {
  const stop = vi.fn();
  const stream = { getTracks: () => [{ stop }] } as unknown as MediaStream;
  const capture = vi.fn(async (_constraints: MediaStreamConstraints) => stream);
  const enumerate = vi.fn(async () => [laptop, headset]);
  const settings = vi.fn(async () => new Response(JSON.stringify({ selected_input: headsetName })));

  beforeEach(() => {
    stop.mockClear();
    capture.mockReset().mockResolvedValue(stream);
    enumerate.mockReset().mockResolvedValue([laptop, headset]);
    settings.mockReset().mockResolvedValue(new Response(JSON.stringify({ selected_input: headsetName })));
    vi.stubGlobal("fetch", settings);
    vi.stubGlobal("navigator", { mediaDevices: { getUserMedia: capture, enumerateDevices: enumerate } });
  });

  afterEach(() => vi.unstubAllGlobals());

  it("opens the pinned headset, not the Windows-default laptop microphone", async () => {
    await expect(openBrowserMicrophone(true)).resolves.toBe(stream);
    expect(capture).toHaveBeenCalledExactlyOnceWith({ audio: {
      deviceId: { exact: "headset-id" }, channelCount: { ideal: 1 },
      echoCancellation: true, noiseSuppression: true, autoGainControl: true,
    } });
    expect(settings).toHaveBeenCalledWith("/api/settings/audio-devices", expect.objectContaining({ cache: "no-store" }));
  });

  it("uses default capture on a remote browser without reading server hardware", async () => {
    await openBrowserMicrophone(false);
    expect(settings).not.toHaveBeenCalled();
    expect(enumerate).not.toHaveBeenCalled();
    expect(capture.mock.calls[0][0].audio).not.toHaveProperty("deviceId");
  });

  it.each(["auto-headset", "auto", ""])("preserves automatic selection for %s", async name => {
    settings.mockResolvedValueOnce(new Response(JSON.stringify({ selected_input: name })));
    await openBrowserMicrophone(true);
    expect(enumerate).not.toHaveBeenCalled();
    expect(capture.mock.calls[0][0].audio).not.toHaveProperty("deviceId");
  });

  it("unlocks labels only after permission and stops the temporary stream", async () => {
    enumerate.mockResolvedValueOnce([input("", "")]).mockResolvedValueOnce([laptop, headset]);
    await openBrowserMicrophone(true);
    expect(capture).toHaveBeenCalledTimes(2);
    expect(stop).toHaveBeenCalledOnce();
    expect(capture.mock.calls[1][0].audio).toHaveProperty("deviceId", { exact: "headset-id" });
  });

  it("releases the permission stream when enumeration fails", async () => {
    enumerate.mockResolvedValueOnce([]).mockRejectedValueOnce(new Error("Device lookup failed"));
    await expect(openBrowserMicrophone(true)).rejects.toThrow("Device lookup failed");
    expect(stop).toHaveBeenCalledOnce();
    expect(capture).toHaveBeenCalledOnce();
  });

  it("never falls back to the laptop if the selected headset is unplugged", async () => {
    enumerate.mockResolvedValueOnce([laptop]);
    await expect(openBrowserMicrophone(true)).rejects.toBeInstanceOf(MicrophoneSelectionError);
    expect(capture).not.toHaveBeenCalled();
  });

  it("keeps microphone permission refusal visible and never retries it", async () => {
    enumerate.mockResolvedValueOnce([]);
    const denied = new DOMException("Microphone denied", "NotAllowedError");
    capture.mockRejectedValueOnce(denied);
    await expect(openBrowserMicrophone(true)).rejects.toBe(denied);
    expect(capture).toHaveBeenCalledOnce();
  });

  it("reads a changed selection again on the next call", async () => {
    await openBrowserMicrophone(true);
    settings.mockResolvedValueOnce(new Response(JSON.stringify({ selected_input: laptop.label })));
    await openBrowserMicrophone(true);
    expect(capture.mock.calls[1][0].audio).toHaveProperty("deviceId", { exact: "laptop-id" });
  });

  it("fails honestly if the settings request fails", async () => {
    settings.mockResolvedValueOnce(new Response("Unavailable", { status: 503 }));
    await expect(openBrowserMicrophone(true)).rejects.toThrow("Could not read the selected microphone");
    expect(capture).not.toHaveBeenCalled();
  });

  it("prefers the physical device to its default and communications aliases", () => {
    expect(selectedBrowserMicrophone([
      input("default", `Default - ${headsetName}`),
      input("communications", `Communications - ${headsetName}`), headset,
    ], headsetName)).toBe(headset);
  });

  it("accepts a unique name token but refuses ambiguous physical devices", () => {
    expect(selectedBrowserMicrophone([laptop, headset], "boAt Rockerz")).toBe(headset);
    expect(selectedBrowserMicrophone([headset, input("other-headset", headsetName)], headsetName)).toBeUndefined();
    expect(selectedBrowserMicrophone([headset, input("other-headset", "Headset (boAt Rockerz Other)")], "boAt Rockerz")).toBeUndefined();
  });

  it("normalizes Unicode and USB label suffixes without matching an output", () => {
    const mic = input("usb-id", "Mic Café (1234:abcd)");
    const output = { ...input("speaker-id", "Mic Café"), kind: "audiooutput" } as MediaDeviceInfo;
    expect(selectedBrowserMicrophone([output, mic], "MIC Cafe\u0301")).toBe(mic);
  });

  it("matches the same Bluetooth endpoint across Windows API route names", () => {
    const hfp = input("hfp-id", "Microphone (boAt Rockerz 255 Pro+ Hands-Free AG Audio)");
    expect(selectedBrowserMicrophone([laptop, hfp], headsetName)).toBe(hfp);
  });

  it("reports a headset disconnected between enumeration and stream opening", async () => {
    capture.mockRejectedValueOnce(new DOMException("Device disappeared", "OverconstrainedError"));
    await expect(openBrowserMicrophone(true)).rejects.toThrow("selected microphone could not be opened");
    expect(capture).toHaveBeenCalledOnce();
  });
});
