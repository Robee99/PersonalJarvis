// Offers this machine's local model server to Pi and Oh My Pi as the
// "jarvis" provider, so a Pi pane opened from the Agentic IDE runs on the
// owner's own Gemma or Qwen at no cost. Loaded with `-e` by the pane launch
// (jarvis/workspace/agents.py); it writes nothing to the CLI's own settings.
//
// The server is the OpenAI-compatible endpoint Jarvis already talks to
// (JARVIS_LOCAL_MODELS_URL, else the model swapper on 127.0.0.1:11443). Its
// model list is read once at start; when the server is off, the default id
// is offered anyway so the pane can be pointed at it once the server runs.

const BASE = (process.env.JARVIS_LOCAL_MODELS_URL || "http://127.0.0.1:11443/v1").replace(/\/+$/, "");
const FALLBACK_IDS = ["gemma-4b-qat"];
const FREE = { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 };

async function servedIds() {
  try {
    const res = await fetch(`${BASE}/models`, { signal: AbortSignal.timeout(2000) });
    if (!res.ok) return FALLBACK_IDS;
    const body = await res.json();
    const ids = (Array.isArray(body && body.data) ? body.data : [])
      .map((m) => (m && typeof m.id === "string" ? m.id : ""))
      .filter(Boolean);
    return ids.length ? ids : FALLBACK_IDS;
  } catch {
    // Server off or slow: the fallback id keeps the provider selectable.
    return FALLBACK_IDS;
  }
}

export default async function (pi) {
  const ids = await servedIds();
  pi.registerProvider("jarvis", {
    name: "Jarvis local",
    baseUrl: BASE,
    // A local server takes no key; Pi only needs a non-empty value.
    apiKey: "local",
    api: "openai-completions",
    models: ids.map((id) => ({
      type: "chat",
      id,
      name: `${id} (local)`,
      reasoning: false,
      input: ["text"],
      cost: FREE,
      contextWindow: 32768,
      maxTokens: 4096,
    })),
  });
}
