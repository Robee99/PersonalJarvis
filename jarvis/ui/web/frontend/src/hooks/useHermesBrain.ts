import { useEventStore } from "@/store/events";

/**
 * Is Hermes Agent the brain right now (ADR-0042)?
 *
 * Mirrors `jarvis/brain/route_policy.py::hermes_is_main_brain`, which is
 * defined by the brain's primary provider id being `hermes`. The frontend
 * already holds that id: `brainProvider`, seeded from `GET /api/brain/status`
 * (`useBrainStatus`) and refreshed on every `BrainProviderSwitched`.
 *
 * With Hermes as the brain, Hermes picks the model, the route, the fallback
 * and runs computer use; voice is forced onto the Pipeline engine (the
 * backend refuses `PUT /api/settings/voice-mode` with `realtime`). Selectors
 * for those decisions do nothing in that mode, so the views hide them and say
 * why, instead of offering a control that silently has no effect.
 */
export function isHermesBrain(provider: string | null | undefined): boolean {
  return (provider ?? "").trim().toLowerCase() === "hermes";
}

export function useHermesBrain(): boolean {
  return useEventStore((s) => isHermesBrain(s.brainProvider));
}
