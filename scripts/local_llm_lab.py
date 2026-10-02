#!/usr/bin/env python
"""Measure, place and benchmark a local MoE model on llama.cpp before trusting it.

Why this exists
---------------
A 35B-A3B MoE model on an 8 GB laptop GPU is fast or slow almost entirely
because of WHERE its weights live: dense and attention layers belong on the
GPU, routed experts are split between VRAM and system RAM, and anything that
fits in neither is streamed from disk on every token. The usual symptom of a
bad split is not an error but an erratic 3-15 tok/s that looks like "the
hardware's ceiling". This script replaces guessing with four read-mostly steps:

``baseline``  read-only snapshot of the machine: CPU, RAM, GPU, driver, VRAM in
              use, competing model runtimes, the biggest resident processes.
``plan``      reads the GGUF's tensor table (``gguf`` package, MIT, from the
              llama.cpp project) and computes how many layers' experts must
              stay on the CPU for a given VRAM budget, and whether the CPU part
              fits in free RAM or will be streamed from disk.
``command``   prints (or ``--launch``es) the ``llama-server`` command for that
              plan: every layer on the GPU, ``--n-cpu-moe N``, one slot, a
              bounded host prompt cache, quantized KV cache.
``bench``     drives a RUNNING server with fixed prompts for N minutes, reads
              llama.cpp's own ``timings`` per reply and samples temperatures,
              clocks and memory once a second. Prompt and generation speed are
              reported separately, and the first minute is compared with the
              last one so thermal throttling shows up as a number.
``sweep``     repeats launch + a short bench over several ``--n-cpu-moe`` and
              thread values and ranks them by sustained generation speed.

Nothing here changes drivers, services, power plans or Jarvis' config. The
only process it starts is ``llama-server`` (``command --launch`` and ``sweep``),
and ``sweep`` stops each server it started.

Usage
-----
    python scripts/local_llm_lab.py baseline --out data/perf
    python scripts/local_llm_lab.py plan --model D:/models/Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf
    python scripts/local_llm_lab.py command --model ... --n-cpu-moe 34 --launch
    python scripts/local_llm_lab.py bench --url http://127.0.0.1:11435 --minutes 10 --out data/perf
    python scripts/local_llm_lab.py sweep --model ... --n-cpu-moe 40,36,32 --threads 8,16
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:  # the repo package is optional so the script also runs from a bare copy
    from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
except Exception:  # noqa: BLE001 — outside the repo there is no window to hide
    NO_WINDOW_CREATIONFLAGS = 0

MIB = 1024 * 1024

#: Routed-expert tensors in llama.cpp's naming (``blk.<i>.ffn_{gate,up,down}_exps``
#: and the fused ``gate_up`` variant). Shared experts (``_shexp``) are dense and
#: stay with the layer on the GPU, exactly as ``--n-cpu-moe`` treats them.
_EXPERT_TENSOR = re.compile(r"^blk\.(\d+)\.ffn_(?:gate_up|gate|up|down)_exps\.")

#: Processes that hold a model in RAM/VRAM or reserve memory for a VM. Two
#: model servers on a 16 GB machine is the most common hidden slowdown.
_COMPETING_RUNTIMES = (
    "ollama",
    "ollama app",
    "lm studio",
    "lmstudio",
    "llama-server",
    "koboldcpp",
    "text-generation-webui",
    "vmmem",
    "vmmemwsl",
    "docker desktop",
    "com.docker.backend",
)

_BENCH_PROMPTS = (
    "In two sentences, what is the capital of Australia and why was it chosen?",
    "Write a Python function that returns the n-th Fibonacci number iteratively.",
    "List five practical ways to reduce RAM usage on a Windows 11 laptop.",
    "Explain the difference between prompt processing and token generation speed.",
    "Summarize the plot of Romeo and Juliet in exactly four sentences.",
)


# ── GGUF layout and offload plan ─────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class ModelLayout:
    """Byte totals of one GGUF, split the way ``--n-cpu-moe`` splits them."""

    path: str
    file_bytes: int
    n_layers: int
    expert_bytes_by_layer: tuple[int, ...]
    other_bytes: int

    @property
    def expert_bytes(self) -> int:
        return sum(self.expert_bytes_by_layer)


def layout_from_tensors(path: str, file_bytes: int, tensors: list[tuple[str, int]]) -> ModelLayout:
    """Group ``(name, n_bytes)`` tensor rows into per-layer expert bytes."""
    by_layer: dict[int, int] = {}
    other = 0
    max_layer = -1
    for name, n_bytes in tensors:
        m = re.match(r"^blk\.(\d+)\.", name)
        if m:
            max_layer = max(max_layer, int(m.group(1)))
        expert = _EXPERT_TENSOR.match(name)
        if expert:
            idx = int(expert.group(1))
            by_layer[idx] = by_layer.get(idx, 0) + int(n_bytes)
        else:
            other += int(n_bytes)
    n_layers = max_layer + 1
    per_layer = tuple(by_layer.get(i, 0) for i in range(n_layers))
    return ModelLayout(path, file_bytes, n_layers, per_layer, other)


def read_layout(model: Path) -> ModelLayout:
    """Read the tensor table of ``model`` (headers only; weights are not loaded)."""
    try:
        from gguf import GGUFReader  # noqa: PLC0415 — optional, script-only dependency
    except ImportError as exc:
        raise SystemExit("The 'plan' step needs the gguf package: pip install gguf") from exc
    reader = GGUFReader(str(model), "r")
    tensors = [(str(t.name), int(t.n_bytes)) for t in reader.tensors]
    return layout_from_tensors(str(model), model.stat().st_size, tensors)


@dataclass(frozen=True, slots=True)
class OffloadPlan:
    n_cpu_moe: int
    gpu_bytes: int
    cpu_expert_bytes: int
    free_ram_bytes: int
    fits_in_ram: bool
    load_mode: str
    notes: tuple[str, ...] = field(default_factory=tuple)


def plan_offload(
    layout: ModelLayout,
    *,
    vram_budget_bytes: int,
    gpu_overhead_bytes: int,
    free_ram_bytes: int,
    ram_reserve_bytes: int,
) -> OffloadPlan:
    """The smallest ``--n-cpu-moe`` whose GPU share fits the VRAM budget.

    ``--n-cpu-moe N`` keeps the experts of the FIRST N layers in system RAM, so
    the GPU holds every non-expert tensor plus the experts of layers N..L-1.
    ``gpu_overhead_bytes`` covers compute buffers, the KV cache and the vision
    projector; it is a measured-on-device knob, not something the GGUF knows.
    """
    notes: list[str] = []
    fixed = layout.other_bytes + gpu_overhead_bytes
    if fixed > vram_budget_bytes:
        notes.append(
            "Even with every expert on the CPU the dense part and buffers exceed the "
            "VRAM budget: lower the context, drop --mmproj offload, or pick a smaller quant."
        )
    room = vram_budget_bytes - fixed
    n_cpu_moe = layout.n_layers
    for n in range(layout.n_layers, -1, -1):
        on_gpu = sum(layout.expert_bytes_by_layer[n:])
        if on_gpu <= room:
            n_cpu_moe = n
        else:
            break
    cpu_expert = sum(layout.expert_bytes_by_layer[:n_cpu_moe])
    gpu_bytes = fixed + sum(layout.expert_bytes_by_layer[n_cpu_moe:])
    fits = cpu_expert + ram_reserve_bytes <= free_ram_bytes
    if fits:
        load_mode = "none"
        notes.append("CPU experts fit in free RAM: load without mmap (-lm none) so nothing pages.")
    else:
        load_mode = "mmap"
        short = (cpu_expert + ram_reserve_bytes - free_ram_bytes) / MIB / 1024
        notes.append(
            f"CPU experts exceed free RAM by about {short:.1f} GiB: llama.cpp will stream "
            "cold experts from disk every token. Expect slow, erratic speed; a smaller "
            "quant that fits will usually be faster."
        )
    return OffloadPlan(
        n_cpu_moe=n_cpu_moe,
        gpu_bytes=gpu_bytes,
        cpu_expert_bytes=cpu_expert,
        free_ram_bytes=free_ram_bytes,
        fits_in_ram=fits,
        load_mode=load_mode,
        notes=tuple(notes),
    )


# ── llama-server command ─────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class ServerProfile:
    model: str
    host: str = "127.0.0.1"
    port: int = 11435
    alias: str = "local-moe"
    ctx: int = 32768
    n_cpu_moe: int | None = None  # None = every expert on the CPU (--cpu-moe)
    threads: int = 8
    threads_batch: int | None = None
    batch: int = 2048
    ubatch: int = 1024
    load_mode: str = "none"
    kv_type: str = "q8_0"
    cache_ram_mib: int = 0
    mmproj: str | None = None
    mmproj_offload: bool = True
    spec_mtp: bool = False
    extra: tuple[str, ...] = ()


def build_command(binary: str, p: ServerProfile) -> list[str]:
    """The ``llama-server`` argv for ``p`` — explicit placement, reproducible runs."""
    cmd = [
        binary,
        "-m",
        p.model,
        "--host",
        p.host,
        "--port",
        str(p.port),
        "--alias",
        p.alias,
        "-ngl",
        "all",
    ]
    if p.n_cpu_moe is None:
        cmd += ["--cpu-moe"]
    else:
        cmd += ["--n-cpu-moe", str(p.n_cpu_moe)]
    cmd += [
        # Explicit placement above; --fit would silently re-decide it per run.
        "--fit",
        "off",
        "-c",
        str(p.ctx),
        # One slot: the default auto slot count multiplies the KV cache.
        "-np",
        "1",
        "-fa",
        "on",
        "-ctk",
        p.kv_type,
        "-ctv",
        p.kv_type,
        "-t",
        str(p.threads),
        "-tb",
        str(p.threads_batch or p.threads),
        "-b",
        str(p.batch),
        "-ub",
        str(p.ubatch),
        "-lm",
        p.load_mode,
        # The host prompt cache defaults to 8 GiB of system RAM: on a 16 GB
        # machine that RAM belongs to the experts.
        "--cache-ram",
        str(p.cache_ram_mib),
        "--jinja",
        "--metrics",
    ]
    if p.mmproj:
        cmd += ["--mmproj", p.mmproj]
        if not p.mmproj_offload:
            cmd += ["--no-mmproj-offload"]
    if p.spec_mtp:
        cmd += ["--spec-type", "draft-mtp"]
    cmd += list(p.extra)
    return cmd


def find_server_binary(explicit: str | None) -> str:
    if explicit:
        return explicit
    found = shutil.which("llama-server")
    if not found:
        raise SystemExit(
            "llama-server not found on PATH: pass --server-bin (winget install ggml.llamacpp)"
        )
    return found


# ── Machine sampling ─────────────────────────────────────────────────────
_NVIDIA_FIELDS = (
    "name,driver_version,memory.total,memory.used,temperature.gpu,"
    "clocks.sm,power.draw,power.limit,utilization.gpu"
)


def parse_nvidia_csv(text: str) -> list[dict[str, str]]:
    """Rows of ``nvidia-smi --query-gpu=... --format=csv,noheader,nounits``."""
    keys = _NVIDIA_FIELDS.split(",")
    rows = []
    for line in text.strip().splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) == len(keys):
            rows.append(dict(zip(keys, parts, strict=True)))
    return rows


def sample_nvidia() -> list[dict[str, str]]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    try:
        out = subprocess.run(  # noqa: S603 — fixed argv, no shell
            [exe, f"--query-gpu={_NVIDIA_FIELDS}", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_nvidia_csv(out.stdout) if out.returncode == 0 else []


def _cpu_temp_c() -> float | None:
    import psutil  # noqa: PLC0415

    reader = getattr(psutil, "sensors_temperatures", None)
    if reader is None:  # Windows: psutil has no sensor API; use HWiNFO's CSV log
        return None
    try:
        temps = reader() or {}
    except Exception:  # noqa: BLE001 — sensors are optional evidence
        return None
    values = [t.current for entries in temps.values() for t in entries if t.current]
    return max(values) if values else None


def baseline() -> dict[str, Any]:
    """Read-only snapshot. Nothing is started, stopped or changed."""
    import platform  # noqa: PLC0415

    import psutil  # noqa: PLC0415

    vm = psutil.virtual_memory()
    sw = psutil.swap_memory()
    procs = []
    competing = []
    for proc in psutil.process_iter(["name", "memory_info"]):
        try:
            name = (proc.info["name"] or "").lower()
            rss = proc.info["memory_info"].rss if proc.info["memory_info"] else 0
        except (psutil.Error, AttributeError):
            continue
        procs.append((rss, name, proc.pid))
        if any(name.startswith(r) for r in _COMPETING_RUNTIMES):
            competing.append({"name": name, "pid": proc.pid, "rss_mib": rss // MIB})
    procs.sort(reverse=True)
    disk_root = Path.home().anchor or "/"
    du = shutil.disk_usage(disk_root)
    return {
        "taken_at": datetime.now(UTC).isoformat(),
        "os": f"{platform.system()} {platform.release()} ({platform.version()})",
        "cpu": platform.processor() or platform.machine(),
        "cores_physical": psutil.cpu_count(logical=False),
        "cores_logical": psutil.cpu_count(logical=True),
        "cpu_freq_mhz": getattr(psutil.cpu_freq(), "current", None),
        "cpu_percent_idle_sample": psutil.cpu_percent(interval=1.0),
        "cpu_temp_c": _cpu_temp_c(),
        "ram_total_mib": vm.total // MIB,
        "ram_available_mib": vm.available // MIB,
        "swap_used_mib": sw.used // MIB,
        "gpus": sample_nvidia(),
        "disk_root": disk_root,
        "disk_free_gib": round(du.free / MIB / 1024, 1),
        "competing_runtimes": competing,
        "top_processes_rss_mib": [
            {"name": n, "pid": pid, "rss_mib": rss // MIB} for rss, n, pid in procs[:15]
        ],
    }


# ── Benchmark ────────────────────────────────────────────────────────────
@dataclass(slots=True)
class Turn:
    t: float
    prompt_n: int
    prompt_tps: float
    gen_n: int
    gen_tps: float
    cache_n: int


def parse_timings(reply: dict[str, Any]) -> tuple[int, float, int, float, int] | None:
    timings = reply.get("timings") if isinstance(reply, dict) else None
    if not isinstance(timings, dict):
        return None
    try:
        return (
            int(timings.get("prompt_n") or 0),
            float(timings.get("prompt_per_second") or 0.0),
            int(timings.get("predicted_n") or 0),
            float(timings.get("predicted_per_second") or 0.0),
            int(timings.get("cache_n") or 0),
        )
    except (TypeError, ValueError):
        return None


def _post_json(url: str, body: dict[str, Any], timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(  # noqa: S310 — caller-supplied local server URL
        url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def wait_healthy(base_url: str, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/health", timeout=5) as resp:  # noqa: S310
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(2)
    return False


def summarize(turns: list[Turn], samples: list[dict[str, Any]], seconds: float) -> dict[str, Any]:
    """Peak versus sustained generation speed, prompt speed apart, drift over time."""

    def pct(values: list[float], q: float) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        return round(ordered[min(len(ordered) - 1, int(q * (len(ordered) - 1)))], 2)

    gen = [t.gen_tps for t in turns if t.gen_n >= 16]
    prompt = [t.prompt_tps for t in turns if t.prompt_n >= 16]
    first = [t.gen_tps for t in turns if t.t <= 60 and t.gen_n >= 16]
    last = [t.gen_tps for t in turns if t.t >= seconds - 60 and t.gen_n >= 16]
    drift = None
    if first and last:
        drift = round((statistics.median(last) / statistics.median(first) - 1) * 100, 1)

    def peak(key: str) -> float | None:
        vals = []
        for s in samples:
            try:
                vals.append(float(s[key]))
            except (KeyError, TypeError, ValueError):
                continue
        return max(vals) if vals else None

    def low(key: str) -> float | None:
        vals = [float(s[key]) for s in samples if isinstance(s.get(key), (int, float))]
        return min(vals) if vals else None

    return {
        "turns": len(turns),
        "generation_tps_peak": round(max(gen), 2) if gen else None,
        "generation_tps_median": round(statistics.median(gen), 2) if gen else None,
        "generation_tps_p10": pct(gen, 0.10),
        "prompt_tps_median": round(statistics.median(prompt), 2) if prompt else None,
        "first_minute_vs_last_minute_pct": drift,
        "gpu_temp_c_max": peak("temperature.gpu"),
        "gpu_mem_used_mib_max": peak("memory.used"),
        "gpu_power_w_max": peak("power.draw"),
        "cpu_temp_c_max": peak("cpu_temp_c"),
        "ram_available_mib_min": low("ram_available_mib"),
        "swap_used_mib_max": peak("swap_used_mib"),
    }


def run_bench(
    base_url: str,
    *,
    minutes: float,
    max_tokens: int,
    out_dir: Path | None,
    label: str,
    request_timeout: float = 600.0,
) -> dict[str, Any]:
    """Drive a running server for ``minutes`` and return the summary."""
    import psutil  # noqa: PLC0415

    seconds = minutes * 60
    turns: list[Turn] = []
    samples: list[dict[str, Any]] = []
    stop = threading.Event()
    start = time.monotonic()

    def sampler() -> None:
        while not stop.is_set():
            row: dict[str, Any] = {"t": round(time.monotonic() - start, 1)}
            gpus = sample_nvidia()
            if gpus:
                row.update(gpus[0])
            vm = psutil.virtual_memory()
            row["ram_available_mib"] = vm.available // MIB
            row["swap_used_mib"] = psutil.swap_memory().used // MIB
            row["cpu_percent"] = psutil.cpu_percent(interval=None)
            row["cpu_temp_c"] = _cpu_temp_c()
            samples.append(row)
            stop.wait(1.0)

    thread = threading.Thread(target=sampler, name="lab-sampler", daemon=True)
    thread.start()
    i = 0
    errors = 0
    try:
        while time.monotonic() - start < seconds:
            prompt = _BENCH_PROMPTS[i % len(_BENCH_PROMPTS)]
            i += 1
            body = {
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": 0.7,
                "stream": False,
                # Generation speed, not thinking length, is what is measured.
                "reasoning_effort": "none",
            }
            try:
                reply = _post_json(f"{base_url}/v1/chat/completions", body, request_timeout)
            except (urllib.error.URLError, OSError, ValueError) as exc:
                errors += 1
                print(f"[bench] request failed: {exc}", file=sys.stderr)
                if errors >= 3:
                    break
                continue
            parsed = parse_timings(reply)
            if parsed is None:
                continue
            p_n, p_tps, g_n, g_tps, c_n = parsed
            turns.append(Turn(round(time.monotonic() - start, 1), p_n, p_tps, g_n, g_tps, c_n))
            print(f"[bench] t={turns[-1].t:>6}s gen={g_tps:6.2f} tok/s prompt={p_tps:8.2f} tok/s")
    finally:
        stop.set()
        thread.join(timeout=5)
    elapsed = time.monotonic() - start
    summary = summarize(turns, samples, elapsed)
    summary.update(
        {"label": label, "url": base_url, "seconds": round(elapsed, 1), "errors": errors}
    )
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        stem = out_dir / f"bench-{label}-{stamp}"
        with open(f"{stem}-turns.csv", "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(Turn.__slots__))
            writer.writeheader()
            for turn in turns:
                writer.writerow({k: getattr(turn, k) for k in Turn.__slots__})
        keys = sorted({k for s in samples for k in s})
        with open(f"{stem}-samples.csv", "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=keys)
            writer.writeheader()
            writer.writerows(samples)
        Path(f"{stem}-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"[bench] wrote {stem}-*.csv / -summary.json")
    return summary


# ── CLI ──────────────────────────────────────────────────────────────────
def _profile_from_args(args: argparse.Namespace, **overrides: Any) -> ServerProfile:
    values = {
        "model": args.model,
        "host": args.host,
        "port": args.port,
        "alias": args.alias,
        "ctx": args.ctx,
        "n_cpu_moe": args.n_cpu_moe if isinstance(args.n_cpu_moe, int) else None,
        "threads": args.threads if isinstance(args.threads, int) else 8,
        "batch": args.batch,
        "ubatch": args.ubatch,
        "load_mode": args.load_mode,
        "kv_type": args.kv_type,
        "cache_ram_mib": args.cache_ram,
        "mmproj": args.mmproj,
        "mmproj_offload": not args.no_mmproj_offload,
        "spec_mtp": args.mtp,
    }
    values.update(overrides)
    return ServerProfile(**values)


def _add_server_args(p: argparse.ArgumentParser, *, sweep: bool = False) -> None:
    p.add_argument("--model", required=True, help="path to the .gguf file")
    p.add_argument("--server-bin", help="llama-server executable (default: from PATH)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=11435)
    p.add_argument("--alias", default="local-moe", help="model id the server reports")
    p.add_argument("--ctx", type=int, default=32768)
    if sweep:
        p.add_argument("--n-cpu-moe", required=True, help="comma list, e.g. 40,36,32")
        p.add_argument("--threads", default="8", help="comma list, e.g. 8,16")
    else:
        p.add_argument("--n-cpu-moe", type=int, help="omit for --cpu-moe (all experts on CPU)")
        p.add_argument("--threads", type=int, default=8)
    p.add_argument("--batch", type=int, default=2048)
    p.add_argument("--ubatch", type=int, default=1024)
    p.add_argument("--load-mode", default="none", choices=["none", "mmap", "mlock", "auto"])
    p.add_argument("--kv-type", default="q8_0")
    p.add_argument("--cache-ram", type=int, default=0, help="host prompt cache MiB")
    p.add_argument("--mmproj", help="vision projector .gguf (enables image input)")
    p.add_argument("--no-mmproj-offload", action="store_true")
    p.add_argument("--mtp", action="store_true", help="MTP speculative decoding (MTP GGUF only)")


def _launch(cmd: list[str]) -> subprocess.Popen[bytes]:
    print("[lab] " + subprocess.list2cmdline(cmd))
    return subprocess.Popen(cmd, creationflags=NO_WINDOW_CREATIONFLAGS)  # noqa: S603


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("baseline", help="read-only machine snapshot")
    b.add_argument("--out", type=Path, help="directory to write baseline-<time>.json")

    pl = sub.add_parser("plan", help="compute --n-cpu-moe and load mode from the GGUF")
    pl.add_argument("--model", required=True, type=Path)
    pl.add_argument("--vram-mib", type=int, help="usable VRAM (default: total minus 600)")
    pl.add_argument("--overhead-mib", type=int, default=1536, help="KV + compute buffers")
    pl.add_argument("--ram-reserve-mib", type=int, default=3072, help="RAM kept for Jarvis")

    c = sub.add_parser("command", help="print or launch the llama-server command")
    _add_server_args(c)
    c.add_argument("--launch", action="store_true")

    be = sub.add_parser("bench", help="sustained benchmark against a running server")
    be.add_argument("--url", default="http://127.0.0.1:11435")
    be.add_argument("--minutes", type=float, default=10.0)
    be.add_argument("--max-tokens", type=int, default=256)
    be.add_argument("--label", default="run")
    be.add_argument("--out", type=Path)

    sw = sub.add_parser("sweep", help="launch + short bench per setting, ranked")
    _add_server_args(sw, sweep=True)
    sw.add_argument("--minutes", type=float, default=2.0)
    sw.add_argument("--max-tokens", type=int, default=256)
    sw.add_argument("--out", type=Path)

    args = parser.parse_args(argv)

    if args.cmd == "baseline":
        snap = baseline()
        text = json.dumps(snap, indent=2)
        print(text)
        if args.out:
            args.out.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            (args.out / f"baseline-{stamp}.json").write_text(text, encoding="utf-8")
        return 0

    if args.cmd == "plan":
        import psutil  # noqa: PLC0415

        layout = read_layout(args.model)
        vram = args.vram_mib
        if vram is None:
            gpus = sample_nvidia()
            if not gpus:
                raise SystemExit("No NVIDIA GPU readable: pass --vram-mib")
            vram = int(float(gpus[0]["memory.total"])) - 600
        plan = plan_offload(
            layout,
            vram_budget_bytes=vram * MIB,
            gpu_overhead_bytes=args.overhead_mib * MIB,
            free_ram_bytes=psutil.virtual_memory().available,
            ram_reserve_bytes=args.ram_reserve_mib * MIB,
        )
        report = {
            "model": layout.path,
            "file_gib": round(layout.file_bytes / MIB / 1024, 2),
            "layers": layout.n_layers,
            "expert_gib": round(layout.expert_bytes / MIB / 1024, 2),
            "non_expert_gib": round(layout.other_bytes / MIB / 1024, 2),
            **asdict(plan),
        }
        print(json.dumps(report, indent=2))
        return 0

    if args.cmd == "command":
        cmd = build_command(find_server_binary(args.server_bin), _profile_from_args(args))
        if not args.launch:
            print(subprocess.list2cmdline(cmd))
            return 0
        return _launch(cmd).wait()

    if args.cmd == "bench":
        summary = run_bench(
            args.url.rstrip("/"),
            minutes=args.minutes,
            max_tokens=args.max_tokens,
            out_dir=args.out,
            label=args.label,
        )
        print(json.dumps(summary, indent=2))
        return 0 if summary["turns"] else 1

    if args.cmd == "sweep":
        binary = find_server_binary(args.server_bin)
        results = []
        base_url = f"http://{args.host}:{args.port}"
        for n in [int(x) for x in str(args.n_cpu_moe).split(",") if x.strip()]:
            for threads in [int(x) for x in str(args.threads).split(",") if x.strip()]:
                profile = _profile_from_args(args, n_cpu_moe=n, threads=threads)
                proc = _launch(build_command(binary, profile))
                try:
                    if not wait_healthy(base_url, timeout_s=600):
                        results.append(
                            {"n_cpu_moe": n, "threads": threads, "error": "did not start"}
                        )
                        continue
                    summary = run_bench(
                        base_url,
                        minutes=args.minutes,
                        max_tokens=args.max_tokens,
                        out_dir=args.out,
                        label=f"ncmoe{n}-t{threads}",
                    )
                    results.append({"n_cpu_moe": n, "threads": threads, **summary})
                finally:
                    proc.terminate()
                    try:
                        proc.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        proc.kill()
        ranked = sorted(results, key=lambda r: r.get("generation_tps_median") or 0, reverse=True)
        print(json.dumps(ranked, indent=2))
        if args.out:
            args.out.mkdir(parents=True, exist_ok=True)
            (args.out / "sweep-ranked.json").write_text(
                json.dumps(ranked, indent=2), encoding="utf-8"
            )
        return 0
    return 2


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    sys.exit(main())
