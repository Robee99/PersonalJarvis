import { Activity, Trash2, Zap } from "lucide-react";
import { ViewHeader } from "@/views/ChatsView";
import { EventTimeline } from "@/components/EventTimeline";
import { CurrentBrainLink } from "@/components/providers/CurrentBrainLink";
import { Button } from "@/components/ui/button";
import { useEventStore } from "@/store/events";
import { useT } from "@/i18n";

export function DebugView() {
  const t = useT();
  const pushEvent = useEventStore((s) => s.pushEvent);
  const clearEvents = useEventStore((s) => s.clearEvents);
  const events = useEventStore((s) => s.events);

  return (
    <div className="flex h-full flex-col">
      <ViewHeader
        icon={<Activity className="h-4 w-4 text-primary" />}
        title={t("debug_view.title")}
        subtitle={`${t("debug_view.subtitle")} · ${events.length} events`}
      />
      <div className="grid flex-1 min-h-0 grid-cols-[1fr_320px] gap-0">
        <div className="flex min-h-0 flex-col border-r border-border">
          <div className="flex items-center gap-2 border-b border-border px-4 py-2">
            <span className="text-micro uppercase tracking-wider text-muted-foreground">
              Event-Timeline
            </span>
            <div className="ml-auto flex gap-1">
              <Button
                size="sm"
                variant="ghost"
                onClick={() =>
                  pushEvent({
                    id: `dbg-${Date.now()}`,
                    name: "debug.test_emit",
                    layer: "ui",
                    ts: Date.now(),
                    payload: { note: "manual emit from Debug view" },
                  })
                }
              >
                <Zap className="h-3.5 w-3.5" />
                <span className="ml-1 text-xs">Emit</span>
              </Button>
              <Button size="sm" variant="ghost" onClick={clearEvents}>
                <Trash2 className="h-3.5 w-3.5" />
                <span className="ml-1 text-xs">Clear</span>
              </Button>
            </div>
          </div>
          <div className="flex-1 min-h-0">
            <EventTimeline />
          </div>
        </div>

        <aside className="flex flex-col overflow-y-auto scrollbar-jarvis">
          <div className="border-b border-border p-4">
            {/* Read-only: the brain is chosen only in Settings › Model. */}
            <h3 className="mb-3 text-micro uppercase tracking-wider text-muted-foreground">
              Brain
            </h3>
            <CurrentBrainLink />
          </div>
          <div className="border-b border-border p-4">
            <h3 className="mb-2 text-micro uppercase tracking-wider text-muted-foreground">
              Flight-Recorder
            </h3>
            <p className="text-xs text-muted-foreground">
              JSONL traces in{" "}
              <code className="font-mono">%APPDATA%\Jarvis\traces\</code> —
              replay player to follow.
            </p>
          </div>
          <div className="p-4">
            <h3 className="mb-2 text-micro uppercase tracking-wider text-muted-foreground">
              Metrics
            </h3>
            <p className="text-xs text-muted-foreground">
              Latency / tokens / cost per provider — live charts from phase 2.
            </p>
          </div>
        </aside>
      </div>
    </div>
  );
}
