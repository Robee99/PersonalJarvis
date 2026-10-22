import { useState } from "react";
import { ChevronDown } from "lucide-react";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { classifyError, humanErrorKey, looksHuman } from "@/lib/humanizeError";

/**
 * A failure said in one short sentence, with the raw output one click away.
 *
 * Infrastructure output (TLS failures, stack traces, CLI dumps, "is the gateway
 * running?") used to be the message itself. It is kept — whoever debugs needs
 * it — but behind a "Details" toggle, collapsed by default. Text that already
 * reads as a sentence is shown unchanged and gets no toggle.
 *
 * Colours are theme tokens only, so the block reads the same in light and dark.
 */
export function HumanError({
  raw,
  prefix,
  className,
  testId = "human-error",
}: {
  /** The raw error text from the backend, a CLI or the runtime. */
  raw: string;
  /** Optional context in front of the sentence ("Voice connect failed"). */
  prefix?: string;
  className?: string;
  testId?: string;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const text = raw.trim();
  const human = looksHuman(text);
  const sentence = human ? text : t(humanErrorKey(classifyError(text)));
  const headline = prefix ? `${prefix}: ${sentence}` : sentence;

  return (
    <div data-testid={testId} className={cn("text-meta", className)}>
      <p role="alert" className="text-foreground">
        {headline}
        {!human && text && (
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            data-testid={`${testId}-toggle`}
            className="ml-2 inline-flex items-center gap-0.5 rounded-sm text-muted-foreground underline-offset-2 hover:text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {t(open ? "errors_human.hide_details" : "errors_human.details")}
            <ChevronDown aria-hidden className={cn("h-3 w-3 transition-transform", open && "rotate-180")} />
          </button>
        )}
      </p>
      {open && !human && (
        <pre
          data-testid={`${testId}-details`}
          className="mt-1.5 max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-md border border-border bg-surface-raised p-2 font-mono text-micro text-muted-foreground scrollbar-jarvis"
        >
          {text}
        </pre>
      )}
    </div>
  );
}
