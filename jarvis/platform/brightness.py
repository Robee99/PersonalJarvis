"""Screen brightness as a voice reflex that only claims what Windows confirms.

"Set the brightness to 40" is a deterministic, low-risk command, so Jarvis
answers it without a model round trip, also when Hermes is the brain. A
setter returning success is not proof that the panel changed, so every change
is read back from Windows and the reply says what the read-back shows:

* the new level when Windows reports it;
* that the change could not be confirmed when it reports something else;
* that this screen's brightness cannot be controlled from here when Windows
  exposes no brightness for it (most external monitors).

Only plain imperatives match. A negation ("don't change the brightness"), a
question ("how do I change the brightness?", "why is brightness stuck?") or
anything with a condition stays with the brain. Windows only: elsewhere the
reflex stands down and the brain handles the turn.
"""

from __future__ import annotations

import logging
import re
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

log = logging.getLogger(__name__)

#: Step used by "brighter" / "turn the brightness down" without a number.
DEFAULT_STEP = 20
#: How far the read-back may differ from the target and still count as set
#: (panels round to their own steps).
TOLERANCE = 3
#: Read-back delay: the WMI value lags the setter by a moment.
SETTLE_S = 0.4

_NEGATION = re.compile(
    r"\b(don'?t|do\s+not|never|not|no\s+need|stop\s+changing|leave)\b", re.IGNORECASE
)
_QUESTION = re.compile(
    r"\?|^\s*(how|why|what|when|where|which|who|is|are|does|do|did|should|could|would|"
    r"can\s+you\s+(tell|explain|show)\s+me)\b",
    re.IGNORECASE,
)
_CONDITION = re.compile(r"\b(if|when|unless|until|after|before|later|tomorrow)\b", re.IGNORECASE)
_SUBJECT = r"(?:the\s+)?(?:screen\s+|display\s+|monitor\s+)?brightness"
_PLEASE = r"(?:(?:please|jarvis|hey\s+jarvis)[,\s]+)*"
_TAIL = r"(?:[,\s]+(?:please|thanks?|thank\s+you|jarvis))*[.!\s]*$"
_SET = re.compile(
    rf"^\s*{_PLEASE}(?:set|change|put|make)\s+{_SUBJECT}\s+(?:to\s+)?(\d{{1,3}})\s*(?:%|percent)?{_TAIL}",
    re.IGNORECASE,
)
_SET_SHORT = re.compile(
    rf"^\s*{_PLEASE}{_SUBJECT}\s+(?:to\s+)?(\d{{1,3}})\s*(?:%|percent)?{_TAIL}", re.IGNORECASE
)
_STEP = re.compile(
    rf"^\s*{_PLEASE}(?:(turn|bring)\s+{_SUBJECT}\s+(up|down)|(turn|bring)\s+(up|down)\s+{_SUBJECT}|"
    rf"(increase|raise|decrease|lower|reduce)\s+{_SUBJECT})"
    rf"(?:\s+by\s+(\d{{1,3}})\s*(?:%|percent)?)?{_TAIL}",
    re.IGNORECASE,
)
_SCREEN = re.compile(
    rf"^\s*{_PLEASE}(?:make\s+(?:the\s+)?(?:screen|display)\s+(brighter|darker|dimmer)|"
    rf"(dim)\s+(?:the\s+)?(?:screen|display)){_TAIL}",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class BrightnessCommand:
    """An unambiguous brightness change: an absolute level or a signed step."""

    level: int | None = None
    step: int = 0


def parse_brightness_command(text: str) -> BrightnessCommand | None:
    """The brightness change this utterance plainly orders, else ``None``."""
    if not text or _NEGATION.search(text) or _QUESTION.search(text) or _CONDITION.search(text):
        return None
    for pattern in (_SET, _SET_SHORT):
        m = pattern.match(text)
        if m:
            return BrightnessCommand(level=max(0, min(100, int(m.group(1)))))
    m = _STEP.match(text)
    if m:
        words = " ".join(g.lower() for g in m.groups()[:5] if g)
        up = any(w in words for w in ("up", "increase", "raise"))
        amount = int(m.group(6)) if m.group(6) else DEFAULT_STEP
        return BrightnessCommand(step=amount if up else -amount)
    m = _SCREEN.match(text)
    if m:
        word = (m.group(1) or m.group(2) or "").lower()
        return BrightnessCommand(step=DEFAULT_STEP if word == "brighter" else -DEFAULT_STEP)
    return None


def _powershell(script: str) -> tuple[int, str]:
    done = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )
    return done.returncode, (done.stdout or "").strip()


def read_brightness(run: Callable[[str], tuple[int, str]] = _powershell) -> int | None:
    """The panel's brightness as Windows reports it, or ``None`` if it has none."""
    code, out = run(
        "(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness "
        "-ErrorAction Stop | Select-Object -First 1).CurrentBrightness"
    )
    if code != 0 or not out.strip().isdigit():
        return None
    return int(out.strip())


def write_brightness(level: int, run: Callable[[str], tuple[int, str]] = _powershell) -> bool:
    code, _ = run(
        "$m = Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods "
        "-ErrorAction Stop | Select-Object -First 1; "
        f"Invoke-CimMethod -InputObject $m -MethodName WmiSetBrightness "
        f"-Arguments @{{Timeout=1; Brightness=[byte]{int(level)}}} | Out-Null"
    )
    return code == 0


def apply_brightness(
    command: BrightnessCommand,
    *,
    run: Callable[[str], tuple[int, str]] = _powershell,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Change the brightness and say what Windows reports afterwards."""
    before = read_brightness(run)
    if before is None:
        return (
            "I can't change the brightness of this screen from here: Windows doesn't "
            "expose its brightness. External monitors usually need their own buttons."
        )
    target = command.level if command.level is not None else before + command.step
    target = max(0, min(100, target))
    if not write_brightness(target, run):
        return f"Windows refused the brightness change; it is still at {before}%."
    sleep(SETTLE_S)
    after = read_brightness(run)
    if after is not None and abs(after - target) <= TOLERANCE:
        return f"Brightness is now {after}%."
    shown = f"{after}%" if after is not None else "unknown"
    return (
        f"I asked Windows for {target}% brightness, but it now reports {shown}, "
        "so I can't confirm the change."
    )


def brightness_reflex(text: str) -> str | None:
    """The reply for a plain brightness command on Windows, else ``None``."""
    command = parse_brightness_command(text)
    if command is None or sys.platform != "win32":
        return None
    try:
        return apply_brightness(command, run=_powershell)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("brightness reflex failed: %s", exc)
        return "I couldn't reach Windows' brightness control, so I can't confirm any change."
