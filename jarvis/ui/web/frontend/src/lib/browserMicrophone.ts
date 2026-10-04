import { hasEmbeddedDesktopBridge } from "./embeddedDesktop";

const AUDIO_SETTINGS_TIMEOUT_MS = 5_000;

export class MicrophoneSelectionError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "MicrophoneSelectionError";
  }
}

function microphoneConstraints(deviceId?: string): MediaStreamConstraints {
  return {
    audio: {
      channelCount: { ideal: 1 },
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
      ...(deviceId ? { deviceId: { exact: deviceId } } : {}),
    },
  };
}

function normalizedLabel(label: string): string {
  return label.normalize("NFC").toLowerCase()
    .replace(/^(?:default|communications)\s*-\s*/, "")
    .replace(/\s*\([0-9a-f]{4}:[0-9a-f]{4}\)\s*$/, "")
    .trim();
}

function physicalLabel(label: string): string {
  return normalizedLabel(label)
    .replace(/^(?:headset|headphones|microphone(?: array)?|mic)\s*\(?\s*/, "")
    .replace(/\bhands[- ]free(?: ag audio)?\b/g, "")
    .replace(/[()]/g, "").replace(/\s+/g, " ").trim();
}

/** Names come from PortAudio; IDs belong to this WebView and must not be persisted. */
export function selectedBrowserMicrophone(
  devices: readonly MediaDeviceInfo[], name: string,
): MediaDeviceInfo | undefined {
  const inputs = devices.filter(d => d.kind === "audioinput" && d.deviceId);
  const physical = inputs.filter(d => !["default", "communications"].includes(d.deviceId));
  const candidates = physical.length ? physical : inputs;
  const wanted = normalizedLabel(name);
  const exact = candidates.filter(d => normalizedLabel(d.label) === wanted);
  if (exact.length === 1) return exact[0];
  if (exact.length > 1 || !wanted) return undefined;
  // The settings contract also permits a shortest-unique device-name token.
  const partial = candidates.filter(d => normalizedLabel(d.label).includes(wanted));
  if (partial.length) return partial.length === 1 ? partial[0] : undefined;
  // Windows APIs can expose the same Bluetooth mic with a different route prefix.
  const physicalWanted = physicalLabel(name);
  if (!physicalWanted) return undefined;
  const sameEndpoint = candidates.filter(d => physicalLabel(d.label) === physicalWanted);
  return sameEndpoint.length === 1 ? sameEndpoint[0] : undefined;
}

async function configuredMicrophoneName(): Promise<string> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), AUDIO_SETTINGS_TIMEOUT_MS);
  try {
    const response = await fetch("/api/settings/audio-devices", {
      cache: "no-store", signal: controller.signal,
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const config = await response.json() as { selected_input?: unknown };
    if (typeof config.selected_input !== "string") throw new Error("Missing microphone selection");
    return config.selected_input.trim();
  } catch (cause) {
    console.warn("Voice microphone selection could not be read.", cause);
    throw new MicrophoneSelectionError(
      "Could not read the selected microphone. Check Audio settings and start again.",
    );
  } finally {
    clearTimeout(timer);
  }
}

/** Honor the desktop's input picker without applying a remote server's hardware to a browser. */
export async function openBrowserMicrophone(
  useHostSelection = hasEmbeddedDesktopBridge(),
): Promise<MediaStream> {
  const media = navigator.mediaDevices;
  if (!useHostSelection) return media.getUserMedia(microphoneConstraints());
  const name = await configuredMicrophoneName();
  if (!name || ["auto", "auto-headset"].includes(name)) {
    return media.getUserMedia(microphoneConstraints());
  }
  if (typeof media.enumerateDevices !== "function") {
    throw new MicrophoneSelectionError("This voice window cannot select a microphone. Open Audio settings.");
  }
  let devices = await media.enumerateDevices();
  // Device labels are hidden until the user grants microphone access. Open a
  // permission-only stream, enumerate while it is live, then always release it.
  // None of its samples ever enters the voice graph or reaches a provider.
  if (!devices.some(d => d.kind === "audioinput" && d.label.trim())) {
    const permissionStream = await media.getUserMedia(microphoneConstraints());
    try {
      devices = await media.enumerateDevices();
    } finally {
      permissionStream.getTracks().forEach(track => track.stop());
    }
  }
  const selected = selectedBrowserMicrophone(devices, name);
  if (!selected) {
    throw new MicrophoneSelectionError(
      "The selected microphone is unavailable or ambiguous. Reconnect it or choose another in Audio settings.",
    );
  }
  // Exact is intentional: an unplugged headset must not silently become the
  // laptop's microphone. Preserve permission errors for the existing UI path.
  try {
    return await media.getUserMedia(microphoneConstraints(selected.deviceId));
  } catch (cause) {
    if (cause instanceof DOMException && ["NotFoundError", "NotReadableError", "OverconstrainedError"].includes(cause.name)) {
      throw new MicrophoneSelectionError(
        "The selected microphone could not be opened. Reconnect it or choose another in Audio settings.",
      );
    }
    throw cause;
  }
}
