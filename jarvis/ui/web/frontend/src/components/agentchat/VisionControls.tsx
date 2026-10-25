import { useEffect, useRef, useState } from "react";
import { getLiveVision, PostureNudgeGate, thumbnail, WATCH_INTERVAL_MS, type VisionSource } from "@/lib/liveVision";
import { LocalPostureTracker } from "@/lib/localPosture";
import { useAgentChatStore } from "@/store/agentChat";
import { useEventStore } from "@/store/events";
import { runningTurn } from "./reduce";
import { JarvisRail } from "@/components/rail/JarvisRail";
import { useRailStore } from "@/store/rail";
import { useT } from "@/i18n";

const muted = () => new URLSearchParams(location.search).get("mute") === "1";
const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);

/** One shared controller survives the chat/voice view switch; never opens audio. */
export function VisionControls() {
  const t = useT();
  const eyes = getLiveVision();
  const [, rerender] = useState(0);
    const [problem, setProblem] = useState("");
  const [tracking, setTracking] = useState("");
  const [notice, setNotice] = useState("");
  const sessionId = useAgentChatStore(s => s.activeSessionId);
  const previousSession = useRef(sessionId);
  const generation = useRef(0);
  const inFlight = useRef(false);
  const owner = useRef(crypto.randomUUID());
  const sequence = useRef(0);
  const cameraOn = eyes.state.webcam === "on";
  const watching = eyes.watching;
  const sourceSignature = (["screen", "webcam"] as const).filter(source => eyes.state[source] === "on").join(",");
  const provider = useAgentChatStore(s => s.activeSession?.provider ?? s.draft.provider);
  const model = useAgentChatStore(s => s.activeSession?.model ?? s.draft.model);

  async function desktopIndicator(visible: boolean) {
    const response = await fetch("/api/live-vision/watch-indicator", { method: "POST", keepalive: !visible,
      signal: visible ? AbortSignal.timeout(2500) : undefined,
      headers: { "content-type": "application/json" }, body: JSON.stringify({
        client_id: owner.current, visible, dark: document.documentElement.classList.contains("dark"),
      }) });
    const body = await response.json() as { available: boolean; detail: string };
    if (!response.ok || visible && !body.available) throw new Error(body.detail || "Desktop indicator unavailable");
  }

  async function announce(kind: "ears-off" | "head-down" | "slouched" | "absent") {
    const response = await fetch("/api/live-vision/notice", { method: "POST",
      headers: { "content-type": "application/json" }, body: JSON.stringify({ kind, silent: muted() }) });
    if (!response.ok) throw new Error("Vision announcement could not be delivered");
    const body = await response.json() as { text: string };
    setNotice(body.text);
  }
  useEffect(() => {
    const changed = () => rerender(n => n+1);
    const unload = () => eyes.dispose();
    window.addEventListener("jarvis-live-vision", changed);
    window.addEventListener("pagehide", unload);
    return () => { generation.current++; window.removeEventListener("jarvis-live-vision", changed);
      window.removeEventListener("pagehide", unload); eyes.dispose(); };
  }, [eyes]);
  useEffect(() => {
    if (previousSession.current && previousSession.current !== sessionId) { generation.current++; eyes.dispose(); }
    previousSession.current = sessionId;
  }, [eyes, sessionId]);
  useEffect(() => {
    if (!sessionId) return;
    const sources = sourceSignature ? sourceSignature.split(",") : [];
    let disposed = false;
    const register = async (enabled: boolean) => {
      const response = await fetch("/api/live-vision/register", { method: "POST", keepalive: !enabled,
        headers: { "content-type": "application/json" }, body: JSON.stringify({
          conversation_id: sessionId, client_id: owner.current, sources: enabled ? sources : [],
          sequence: ++sequence.current,
          quiet_for_ms: Math.min(180_000, Math.max(0, eyes.quietUntil-Date.now())),
        }) });
      if (!response.ok) {
        const body = await response.json().catch(() => ({})) as { detail?: string };
        throw new Error(body.detail || "Live visual registration failed");
      }
    };
    if (sources.length) void register(true).catch(error => {
      if (!disposed) { eyes.dispose(); setProblem(errorText(error)); }
    });
    const timer = sources.length ? setInterval(() => void register(true).catch(error => {
      if (!disposed) { eyes.dispose(); setProblem(errorText(error)); }
    }), WATCH_INTERVAL_MS) : undefined;
    return () => { disposed = true; if (timer) clearInterval(timer);
      void register(false).catch(error => console.info("Visual source stop:", error)); };
  }, [eyes, sessionId, sourceSignature, provider, model, eyes.quietUntil]);
  useEffect(() => {
    type FrameRequest = { request_id: string; conversation_id: string; client_id: string; sources: VisionSource[] };
    const frameRequest = (event: Event) => {
      const request = (event as CustomEvent<FrameRequest>).detail;
      if (request.client_id !== owner.current || request.conversation_id !== useAgentChatStore.getState().activeSessionId) return;
      let frames;
      let error = "";
      try { frames = request.sources.map(source => eyes.capture(source)); }
      catch (failure) { error = errorText(failure).slice(0, 200); }
      void fetch("/api/live-vision/frames", { method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ request_id: request.request_id, conversation_id: request.conversation_id,
          client_id: owner.current, frames: frames ?? [], error }) }).then(async response => {
        if (!response.ok) throw new Error("Fresh visual frame was refused or expired");
      }).catch(failure => setProblem(errorText(failure)));
    };
    const quietRequest = (event: Event) => {
      const request = (event as CustomEvent<FrameRequest>).detail;
      if (request.client_id === owner.current && request.conversation_id === useAgentChatStore.getState().activeSessionId) {
        eyes.relieve("give me a minute");
      }
    };
    window.addEventListener("jarvis-vision-frame-request", frameRequest);
    window.addEventListener("jarvis-vision-quiet-request", quietRequest);
    return () => { window.removeEventListener("jarvis-vision-frame-request", frameRequest);
      window.removeEventListener("jarvis-vision-quiet-request", quietRequest); };
  }, [eyes]);
  useEffect(() => {
    if (!watching) return;
    const timer = setInterval(() => {
      rerender(value => value+1);
      void desktopIndicator(true).catch(() => setNotice(t("rail.vision.no_badge")));
    }, WATCH_INTERVAL_MS);
    return () => { clearInterval(timer); void desktopIndicator(false).catch(error => console.info("Watch indicator stop:", error)); };
  }, [eyes, watching]);

  useEffect(() => {
    let disposed = false;
    let tracker: LocalPostureTracker | undefined;
    const posture = new PostureNudgeGate();
    let timer: ReturnType<typeof setInterval> | undefined;
    if (cameraOn) {
      setTracking(t("rail.vision.checking"));
      void LocalPostureTracker.create().then(value => {
        if (disposed) { value.close(); return; }
        tracker = value; setTracking(t("rail.vision.tracking"));
        timer = setInterval(() => {
          const video = eyes.videos.webcam;
          if (!video || video.readyState < 2 || Date.now() < eyes.quietUntil) return;
          try {
            const kind = posture.sample(value.sample(video, performance.now()), Date.now());
            if (kind) { posture.accepted(Date.now()); void announce(kind).catch(error => setProblem(errorText(error))); }
          } catch (error) { setTracking(errorText(error)); if (timer) clearInterval(timer); }
        }, 200);
      }).catch(error => { if (!disposed) setTracking(errorText(error)); });
    } else setTracking("");
    return () => { disposed = true; if (timer) clearInterval(timer); tracker?.close(); };
  }, [cameraOn, eyes]);

  useEffect(() => {
    const timer = setInterval(() => {
      const chat = useAgentChatStore.getState();
      const video = eyes.videos.screen;
      if (!eyes.watching || !video || inFlight.current || Date.now() < eyes.quietUntil ||
          chat.busy || runningTurn(chat.timeline) || !chat.activeSessionId) return;
      try {
        if (!eyes.watch.sample(thumbnail(video), Date.now())) return;
        const epoch = generation.current;
        inFlight.current = true;
        void chat.send("Screen Watch: this display has been unchanged for at least sixty seconds. " +
          "Use only the supplied current screen image to offer one brief, useful suggestion about what I might be stuck on. " +
          "If the image gives no useful evidence, say so. Do not take actions.", [], [], undefined,
          { frames: [eyes.capture("screen")], nudge: true, silent: muted() }).then(result => {
            if (epoch === generation.current && result === "sent") eyes.watch.accepted(Date.now());
          }).catch(error => setProblem(errorText(error))).finally(() => { inFlight.current = false; });
      } catch (error) { setProblem(errorText(error)); }
    }, WATCH_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [eyes]);

  async function toggle(source: VisionSource) {
    setProblem(""); setNotice("");
    if (eyes.state[source] !== "off") { if (source === "screen") generation.current++; eyes.stop(source); return; }
    try {
      await eyes.start(source);
      if (eyes.videos[source] && useEventStore.getState().voiceState === "idle") await announce("ears-off");
    } catch (error) { setProblem(errorText(error)); }
  }
  async function watch() {
    setProblem("");
    if (eyes.watching) { generation.current++; eyes.disableWatch(); return; }
    const epoch = generation.current;
    try {
      if (eyes.state.screen !== "on") await eyes.start("screen");
      eyes.enableWatch();
    } catch (error) { eyes.disableWatch(); setProblem(errorText(error)); return; }
    // The desktop badge is a courtesy: the browser's own share bar already
    // shows the screen is shared, so a missing badge never stops the watch.
    try {
      await desktopIndicator(true);
      if (epoch !== generation.current || !eyes.watching) await desktopIndicator(false);
    } catch { setNotice(t("rail.vision.no_badge")); }
  }
  /** Everything off at once: both shares, the watch, the pause, the notes, HOLO. */
  function stopAll() {
    generation.current++;
    eyes.quietUntil = 0;
    eyes.dispose();
    setProblem(""); setNotice(""); setTracking("");
    useRailStore.getState().setHolo(false);
    rerender(n => n+1);
  }
  const quiet = Date.now() < eyes.quietUntil;
  const live = (["webcam", "screen"] as const).filter(s => eyes.state[s] === "on");
  const detail = live.length || eyes.watching || quiet || tracking || notice || problem ? <>
    {live.map(source =>
      <div key={source}><div className="text-muted-foreground">{t(source === "webcam" ? "rail.vision.webcam" : "rail.vision.screen")}</div>
        <video muted autoPlay playsInline aria-label={t(source === "webcam" ? "rail.vision.webcam" : "rail.vision.screen")} className="max-h-28 w-full rounded-md object-contain"
          ref={element => { if (element) { element.srcObject = eyes.videos[source]?.srcObject ?? null; } }} /></div>)}
    {eyes.watching && <div className="flex items-center gap-2"><img src="/jarvis-gigi-256.png" alt="" className="h-7 w-7 rounded-full" /> {t("rail.vision.comparing")}</div>}
    {quiet && <p>{t("rail.vision.paused")}</p>}
    {tracking && <p className="text-muted-foreground">{tracking}</p>}
    {notice && <p role="status">{notice}</p>}
    {problem && <p role="alert">{problem}</p>}
  </> : null;
  return <JarvisRail vision={{
    webcam: cameraOn, screen: eyes.state.screen === "on", watching: eyes.watching,
    onEyes: () => void toggle("webcam"), onScreen: () => void toggle("screen"), onWatch: () => void watch(),
    onQuiet: () => eyes.relieve("give me a minute"), onStopAll: stopAll,
    detail,
  }} />;
}
