"""One local model in VRAM at a time, swapped on demand.

Qwen3-VL 8B is the main brain and stays warm. Gemma, Qwen 35B-A3B and
UI-Venus 2 (screen grounding) are on-demand profiles: the active model is
unloaded before another profile is loaded, and an on-demand profile hands the
GPU back to Qwen3-VL after QWEN_IDLE_S without requests. Qwen 35B-A3B uses its
guarded launcher; the others run llama-server directly.

Front ports keep the addresses Hermes already uses:
  127.0.0.1:11438 -> Gemma (backend 11448)
  127.0.0.1:11437 -> Qwen  (backend 11447)
  127.0.0.1:11440 -> Qwen3-VL 8B (backend 11449)
  127.0.0.1:11441 -> UI-Venus 2 9B (backend 11451)

Before unloading Gemma for Qwen, the same RAM check Qwen-Server.ps1 makes is
done up front (available RAM plus what Gemma frees must reach the guard), so
a swap that would be refused never costs Gemma its warm cache.
Log: C:\\models\\model-swap.log. No key is ever written to the log.

Only one copy of each model may exist (2026-10-12: after a reboot a startup
script loaded a second Gemma straight onto 11438 beside this service's own,
16 GB were committed to two Gemmas and Jarvis's speech recognizer could not
load). So this service is single-instance (a named mutex), and at start and
every idle tick it closes any llama-server that sits on a front port or
runs the Gemma weights outside the backend port.
"""

from __future__ import annotations

import asyncio
import ctypes
import logging
import re
import subprocess
import time
from pathlib import Path

import aiohttp
from aiohttp import web

WORK = Path(
    r"C:\Users\rober\Documents\Codex\2026-10-04\referenced-chatgpt-conversation-this-is-an\work"
)
RUNTIME = WORK / r"runtimes\cuda-b11388\llama-server.exe"
QWEN_VL_RUNTIME = WORK / r"runtimes\turboquant-v0.4.0\llama-server.exe"
SNAP = Path(
    r"C:\Users\rober\.cache\huggingface\hub\models--unsloth--gemma-4-12B-it-qat-GGUF"
    r"\snapshots\980b060c40a8539ac159e0501a3e0f66a6365af3"
)
QWEN_SCRIPT = WORK / "Qwen-Server.ps1"
QWEN_KEY = WORK / "qwen-api-key.txt"
QWEN_VL_SNAP = Path(
    r"C:\Users\rober\.cache\huggingface\hub\models--unsloth--Qwen3-VL-8B-Instruct-GGUF"
    r"\snapshots\b93a7ee713758252c555be4210c00540df954dc2"
)
#: Qwen-Server.ps1's protected profile: WorkingSetLimitMiB 8704 plus 1024 headroom.
QWEN_RAM_NEEDED_MIB = 8704 + 1024
QWEN_VL_RAM_NEEDED_MIB = 6144
UI_VENUS_DIR = Path(r"C:\Users\rober\Downloads\UI-Venus-2-9B")
#: UI-Venus runs fully on the GPU without mmap; RAM holds the runtime only.
UI_VENUS_RAM_NEEDED_MIB = 3072
QWEN_IDLE_S = 180.0
LOAD_TIMEOUT_S = 420.0

GEMMA, QWEN, QWEN_VL, UI_VENUS = "gemma", "qwen", "qwen3-vl", "ui-venus"
FRONT = {GEMMA: 11438, QWEN: 11437, QWEN_VL: 11440, UI_VENUS: 11441}
BACK = {GEMMA: 11448, QWEN: 11447, QWEN_VL: 11449, UI_VENUS: 11451}
MODEL_ID = {
    GEMMA: "gemma-4-12b-qat",
    QWEN: "qwen",
    QWEN_VL: "qwen3-vl-8b",
    UI_VENUS: "ui-venus-2-9b",
}
#: The context window each profile is started with (its -c / --ctx-size).
CTX = {GEMMA: 65536, QWEN: 65536, QWEN_VL: 65536, UI_VENUS: 16384}
#: Where a finished completion reports its size: llama-server's timings
#: (cache_n + prompt_n + predicted_n), or the OpenAI usage block.
_TIMINGS_RE = re.compile(
    rb'"cache_n"\s*:\s*(\d+).{0,400}?"prompt_n"\s*:\s*(\d+).{0,400}?"predicted_n"\s*:\s*(\d+)',
    re.S,
)
_USAGE_RE = re.compile(
    rb'"completion_tokens"\s*:\s*(\d+)\s*,\s*"prompt_tokens"\s*:\s*(\d+)'
    rb'|"prompt_tokens"\s*:\s*(\d+)\s*,\s*"completion_tokens"\s*:\s*(\d+)'
)

#: The brain Hermes and JARVIS talk to; every other profile hands the GPU back
#: to it when idle.
MAIN = QWEN_VL

logging.basicConfig(
    filename=r"C:\models\model-swap.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("model-swap")


class _MemStatus(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def available_mib() -> int:
    status = _MemStatus()
    status.dwLength = ctypes.sizeof(_MemStatus)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
    return int(status.ullAvailPhys // (1024 * 1024))


def _ps(command: str, timeout: float = 600) -> str:
    done = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return (done.stdout or "") + (done.stderr or "")


def close_strays(own_pid: int = 0) -> str:
    """Close llama-servers that would double a model this service owns.

    A llama-server on a FRONT port hijacked the proxy's address; one running
    the Gemma weights on any port but BACK[GEMMA] is a second Gemma.
    Returns what was closed (empty when nothing was).
    """
    fronts = ",".join(str(p) for p in FRONT.values())
    gemma_backend = BACK[GEMMA]
    return _ps(
        "$fronts=@(" + fronts + ");"
        "Get-CimInstance Win32_Process -Filter \"Name='llama-server.exe'\" | ForEach-Object {"
        " $p=$_; if ($p.ProcessId -eq " + str(own_pid) + ") { return };"
        " $line=$p.CommandLine;"
        " $onFront=$false; foreach($port in $fronts){"
        " if($line -match ('--port(?:\\s+|=)' + $port + '(?:\\s|$)')){$onFront=$true} };"
        " $gemma=$line -like '*gemma-4-12B-it-qat*';"
        f" $ownBack=$line -match ('--port(?:\\s+|=){gemma_backend}(?:\\s|$)');"
        " if ($onFront -or ($gemma -and -not $ownBack)) {"
        "  Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue;"
        "  'closed ' + $p.ProcessId + ' command=' + $line } }",
        timeout=20,
    ).strip()


def single_instance() -> bool:
    """True for the first model-swap process; a second one must exit."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    _MUTEX.append(kernel32.CreateMutexW(None, False, "Local\\JarvisModelSwap"))
    return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS


_MUTEX: list[int] = []  # held for the life of the process


def _working_set_mib(port: int) -> int:
    out = _ps(
        f"$c=Get-NetTCPConnection -LocalPort {port} -State Listen "
        "-ErrorAction SilentlyContinue | select -First 1;"
        "if($c){[int]((Get-Process -Id $c.OwningProcess).WorkingSet64/1MB)}else{0}",
        timeout=30,
    ).strip()
    return int(out) if out.isdigit() else 0


class Swapper:
    def __init__(self) -> None:
        self.active: str | None = None
        self.lock = asyncio.Lock()
        self.inflight = {name: 0 for name in FRONT}
        self.last_used = {name: 0.0 for name in FRONT}
        self.gemma_proc: subprocess.Popen | None = None
        self.qwen_vl_proc: subprocess.Popen | None = None
        self.ui_venus_proc: subprocess.Popen | None = None
        self.session: aiohttp.ClientSession | None = None
        #: The newest agent turn's context fill, for the HUD's context meter.
        self.context: dict[str, object] | None = None

    async def healthy(self, name: str) -> bool:
        assert self.session is not None
        try:
            async with self.session.get(
                f"http://127.0.0.1:{BACK[name]}/health", timeout=aiohttp.ClientTimeout(total=3)
            ) as r:
                return r.status == 200
        except Exception:  # noqa: BLE001
            return False

    async def wait_ready(self, name: str) -> None:
        deadline = time.monotonic() + LOAD_TIMEOUT_S
        while time.monotonic() < deadline:
            if await self.healthy(name):
                return
            await asyncio.sleep(1.0)
        raise RuntimeError(f"{name} did not become ready in {LOAD_TIMEOUT_S:.0f}s")

    def start_gemma(self) -> None:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        args = [
            str(RUNTIME),
            "--model",
            str(SNAP / "gemma-4-12B-it-qat-UD-Q4_K_XL.gguf"),
            "--mmproj",
            str(SNAP / "mmproj-F16.gguf"),
            "--alias",
            MODEL_ID[GEMMA],
            "--host",
            "127.0.0.1",
            "--port",
            str(BACK[GEMMA]),
            "--ctx-size",
            "65536",
            "--parallel",
            "1",
            "--batch-size",
            "512",
            "--ubatch-size",
            "512",
            "--flash-attn",
            "on",
            "--cache-type-k",
            "q4_0",
            "--cache-type-v",
            "q4_0",
            "--fit",
            "on",
            "--fit-target",
            "256",
            "--jinja",
            "--reasoning",
            "off",
            "--no-ui",
            "--offline",
            "--log-file",
            rf"C:\models\gemma-server-{stamp}.log",
        ]
        self.gemma_proc = subprocess.Popen(  # noqa: S603
            args,
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def stop_gemma(self) -> None:
        if self.gemma_proc and self.gemma_proc.poll() is None:
            self.gemma_proc.terminate()
            try:
                self.gemma_proc.wait(20)
            except subprocess.TimeoutExpired:
                self.gemma_proc.kill()
        # A Gemma started before this service (or by hand) on the backend port.
        _ps(
            "$port=" + str(BACK[GEMMA]) + ";"
            "Get-CimInstance Win32_Process -Filter \"Name='llama-server.exe'\" | "
            "Where-Object { $_.CommandLine -match ('--port(?:\\s+|=)' + $port + '(?:\\s|$)') } | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }",
            timeout=20,
        )
        self.gemma_proc = None

    def start_qwen(self) -> str:
        return _ps(
            f"& '{QWEN_SCRIPT}' -Action Start -Model 35B -Use65K -Port {BACK[QWEN]}",
            timeout=LOAD_TIMEOUT_S + 60,
        )

    def stop_qwen(self) -> str:
        return _ps(f"& '{QWEN_SCRIPT}' -Action Stop", timeout=60)

    def start_qwen_vl(self) -> None:
        model = QWEN_VL_SNAP / "Qwen3-VL-8B-Instruct-UD-Q4_K_XL.gguf"
        mmproj = QWEN_VL_SNAP / "mmproj-F16.gguf"
        missing = [str(path) for path in (QWEN_VL_RUNTIME, model, mmproj) if not path.is_file()]
        if missing:
            raise FileNotFoundError("Qwen3-VL startup files are missing: " + ", ".join(missing))
        stamp = time.strftime("%Y%m%d-%H%M%S")
        args = [
            str(QWEN_VL_RUNTIME),
            "-m",
            str(model),
            "--mmproj",
            str(mmproj),
            "--alias",
            MODEL_ID[QWEN_VL],
            "--host",
            "127.0.0.1",
            "--port",
            str(BACK[QWEN_VL]),
            "-c",
            "65536",
            "-np",
            "1",
            "-fa",
            "on",
            # All layers + 64k turbo3 KV on the GPU; --fit put layers on the
            # CPU (36 tok/s prompt). mmproj on CPU keeps the KV in 8 GB VRAM.
            "-ngl",
            "99",
            "--no-mmproj-offload",
            # All layers live in VRAM; without mmap the 4.4 GB file is not
            # kept in RAM after the upload.
            "--no-mmap",
            # turbo3 K returned empty output past ~10k tokens; turbo4 K holds at 40k.
            "-ctk",
            "turbo4",
            "-ctv",
            "turbo3",
            "--image-min-tokens",
            "1024",
            "-t",
            "8",
            "-tb",
            "8",
            "-b",
            "1024",
            "-ub",
            "512",
            "--cache-ram",
            "1024",
            "--jinja",
            "--no-ui",
            "--offline",
            "--log-file",
            rf"C:\models\qwen3-vl-server-{stamp}.log",
        ]
        self.qwen_vl_proc = subprocess.Popen(  # noqa: S603
            args,
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def stop_qwen_vl(self) -> None:
        if self.qwen_vl_proc and self.qwen_vl_proc.poll() is None:
            self.qwen_vl_proc.terminate()
            try:
                self.qwen_vl_proc.wait(20)
            except subprocess.TimeoutExpired:
                self.qwen_vl_proc.kill()
        _ps(
            "$port=" + str(BACK[QWEN_VL]) + ";"
            "Get-CimInstance Win32_Process -Filter \"Name='llama-server.exe'\" | "
            "Where-Object { $_.CommandLine -match ('--port(?:\\s+|=)' + $port + '(?:\\s|$)') } | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }",
            timeout=20,
        )
        self.qwen_vl_proc = None

    def start_ui_venus(self) -> None:
        model = UI_VENUS_DIR / "UI-Venus-2-9B-Q4_K_L.gguf"
        mmproj = UI_VENUS_DIR / "mmproj-UI-Venus-2-9B-f16.gguf"
        missing = [str(path) for path in (RUNTIME, model, mmproj) if not path.is_file()]
        if missing:
            raise FileNotFoundError("UI-Venus startup files are missing: " + ", ".join(missing))
        stamp = time.strftime("%Y%m%d-%H%M%S")
        args = [
            str(RUNTIME),
            "-m",
            str(model),
            "--mmproj",
            str(mmproj),
            "--alias",
            MODEL_ID[UI_VENUS],
            "--host",
            "127.0.0.1",
            "--port",
            str(BACK[UI_VENUS]),
            # Screen grounding: one screenshot plus a short instruction.
            "-c",
            "16384",
            "-np",
            "1",
            "-fa",
            "on",
            "-ngl",
            "99",
            # cuda-b11388 has no --no-mmap; -lm none is its equivalent.
            "-lm",
            "none",
            "-ctk",
            "q8_0",
            "-ctv",
            "q8_0",
            "-t",
            "8",
            "--jinja",
            "--no-ui",
            "--offline",
            "--log-file",
            rf"C:\models\ui-venus-server-{stamp}.log",
        ]
        self.ui_venus_proc = subprocess.Popen(  # noqa: S603
            args,
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def stop_ui_venus(self) -> None:
        if self.ui_venus_proc and self.ui_venus_proc.poll() is None:
            self.ui_venus_proc.terminate()
            try:
                self.ui_venus_proc.wait(20)
            except subprocess.TimeoutExpired:
                self.ui_venus_proc.kill()
        _ps(
            "$port=" + str(BACK[UI_VENUS]) + ";"
            "Get-CimInstance Win32_Process -Filter \"Name='llama-server.exe'\" | "
            "Where-Object { $_.CommandLine -match ('--port(?:\\s+|=)' + $port + '(?:\\s|$)') } | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }",
            timeout=20,
        )
        self.ui_venus_proc = None

    def stop_profile(self, name: str) -> None:
        if name == GEMMA:
            self.stop_gemma()
        elif name == QWEN:
            self.stop_qwen()
        elif name == QWEN_VL:
            self.stop_qwen_vl()
        elif name == UI_VENUS:
            self.stop_ui_venus()

    def start_profile(self, name: str) -> None:
        if name == GEMMA:
            self.start_gemma()
        elif name == QWEN:
            self.start_qwen()
        elif name == QWEN_VL:
            self.start_qwen_vl()
        elif name == UI_VENUS:
            self.start_ui_venus()

    async def ensure(self, name: str) -> None:
        async with self.lock:
            if self.active == name and await self.healthy(name):
                return
            # Never pull the model out from under a request that is running.
            while any(count for profile, count in self.inflight.items() if profile != name):  # noqa: ASYNC110
                await asyncio.sleep(0.5)
            if name == QWEN:
                freed = _working_set_mib(BACK[GEMMA]) if self.active == GEMMA else 0
                projected = available_mib() + freed
                if projected < QWEN_RAM_NEEDED_MIB:
                    raise MemoryError(
                        f"Qwen needs {QWEN_RAM_NEEDED_MIB} MiB free RAM (its tested guard); "
                        f"only about {projected} MiB would be free. Gemma keeps the turn."
                    )
            elif name == QWEN_VL:
                freed = _working_set_mib(BACK[self.active]) if self.active in BACK else 0
                projected = available_mib() + freed
                if projected < QWEN_VL_RAM_NEEDED_MIB:
                    raise MemoryError(
                        f"Qwen3-VL needs {QWEN_VL_RAM_NEEDED_MIB} MiB free RAM; "
                        f"only about {projected} MiB would be free. "
                        "The active model keeps the turn."
                    )
            elif name == UI_VENUS:
                freed = _working_set_mib(BACK[self.active]) if self.active in BACK else 0
                projected = available_mib() + freed
                if projected < UI_VENUS_RAM_NEEDED_MIB:
                    raise MemoryError(
                        f"UI-Venus needs {UI_VENUS_RAM_NEEDED_MIB} MiB free RAM; "
                        f"only about {projected} MiB would be free. "
                        "The active model keeps the turn."
                    )
            started = time.monotonic()
            log.info("swap %s -> %s", self.active, name)
            # Only one profile owns VRAM. Stop every non-target process before
            # starting the requested model, including a stale process after a crash.
            for profile in FRONT:
                if profile != name:
                    await asyncio.to_thread(self.stop_profile, profile)
            self.active = None
            result = await asyncio.to_thread(self.start_profile, name)
            if name == QWEN:
                log.info("qwen launcher: %s", str(result).strip()[-300:])
            try:
                await self.wait_ready(name)
            except Exception:
                # A failed profile stays cold; never leave a half-loaded model
                # competing with the next provider request.
                await asyncio.to_thread(self.stop_profile, name)
                self.active = None
                raise
            self.active = name
            log.info("swap done: %s ready in %.1fs", name, time.monotonic() - started)

    async def idle_loop(self) -> None:
        tick = 0
        while True:
            await asyncio.sleep(15)
            tick += 1
            if tick % 4 == 0 and not self.lock.locked():
                own = self.gemma_proc.pid if self.gemma_proc else 0
                closed = await asyncio.to_thread(close_strays, own)
                if closed:
                    log.warning("closed a duplicate model server: %s", closed.replace("\n", "; "))
            active = self.active
            if (
                # Qwen3-VL is the main brain and stays loaded: once unloaded,
                # the RAM guard refused every reload while JARVIS and Chrome ran.
                # Every other profile hands the GPU back to it after idling.
                active is not None
                and active != MAIN
                and self.inflight[active] == 0
                and time.monotonic() - self.last_used[active] > QWEN_IDLE_S
            ):
                try:
                    async with self.lock:
                        log.info("%s idle - unloading", active)
                        await asyncio.to_thread(self.stop_profile, active)
                        self.active = None
                    await self.ensure(MAIN)
                    log.info("%s warm again after %s idled", MAIN, active)
                except Exception:  # noqa: BLE001
                    log.exception("could not unload Qwen after idle")


swapper = Swapper()


_last_seen: dict[str, tuple[str, str]] = {}


def _first_diff(a: str, b: str) -> int:
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            return i
    return n


def _log_shape(name: str, body: bytes) -> None:
    """Who is calling, by shape only: system prompt, tools, message count.

    llama.cpp reuses the slot's cache up to the first changed token, and the
    tool schemas sit after the system prompt, so a system prompt that changes
    between turns re-reads ~30k tokens (60-90 s). This logs where the system
    prompt or the tools first changed (a character index), never their text.
    """
    import hashlib
    import json

    try:
        data = json.loads(body or b"{}")
        messages = data.get("messages") or []
        system = str(messages[0].get("content") or "") if messages else ""
        tools = data.get("tools") or []
        tools_text = json.dumps(tools)
        sys_hash = hashlib.sha256(system.encode("utf-8", "replace")).hexdigest()[:10]
        tools_hash = hashlib.sha256(tools_text.encode("utf-8", "replace")).hexdigest()[:10]
        prev = _last_seen.get(name)
        note = ""
        if prev and prev[0] != system:
            note += f" system_changed_at={_first_diff(prev[0], system)}/{len(system)}"
        if prev and prev[1] != tools_text:
            note += f" tools_changed_at={_first_diff(prev[1], tools_text)}/{len(tools_text)}"
        _last_seen[name] = (system, tools_text)
        log.info(
            "%s request: system=%s tools=%s(%d) messages=%d bytes=%d%s",
            name,
            sys_hash,
            tools_hash,
            len(tools),
            len(messages),
            len(body),
            note,
        )
        if prev is None or prev[1] != tools_text:
            # Tool names and schema sizes only, to see what the prompt costs.
            rows = sorted(
                ((len(json.dumps(t)), (t.get("function") or {}).get("name", "?")) for t in tools),
                reverse=True,
            )
            Path(r"C:\models\last-request-tools.txt").write_text(
                f"{name} system_chars={len(system)} tool_bytes={len(tools_text)}\n"
                + "".join(f"{size}\t{tool}\n" for size, tool in rows),
                encoding="utf-8",
            )
    except Exception:  # noqa: BLE001
        log.info("%s request: %d bytes (not JSON)", name, len(body))


def context_used(tail: bytes) -> int | None:
    """Tokens a finished completion held in context, read from its last bytes.

    The last timings block wins: a stream repeats none, but a tail can hold
    the end of an earlier chunk.
    """
    timings = _TIMINGS_RE.findall(tail)
    if timings:
        cache_n, prompt_n, predicted_n = timings[-1]
        return int(cache_n) + int(prompt_n) + int(predicted_n)
    usage = _USAGE_RE.findall(tail)
    if usage:
        out_a, in_a, in_b, out_b = usage[-1]
        return int(in_a or in_b) + int(out_a or out_b)
    return None


def handler_for(name: str):
    async def handle(request: web.Request) -> web.StreamResponse:
        path = request.match_info.get("tail", "")
        # Probes never load weights: answer them here.
        if request.method == "GET" and path in ("v1/models", "models"):
            return web.json_response(
                {
                    "object": "list",
                    "data": [{"id": MODEL_ID[name], "object": "model", "owned_by": "llamacpp"}],
                }
            )
        if request.method == "GET" and path == "health":
            return web.json_response({"status": "ok", "loaded": swapper.active == name})
        if request.method == "GET" and path == "swap/status":
            return web.json_response(
                {
                    "active": swapper.active,
                    "inflight": swapper.inflight,
                    "context": swapper.context,
                }
            )
        try:
            await swapper.ensure(name)
        except MemoryError as exc:
            log.warning("%s refused: %s", name, exc)
            return web.json_response(
                {"error": {"message": str(exc), "type": "unavailable"}}, status=503
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("%s could not be loaded", name)
            return web.json_response(
                {"error": {"message": f"{name} could not be loaded: {exc}"}}, status=503
            )
        body = await request.read()
        if path.endswith("chat/completions"):
            _log_shape(name, body)
        headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower() not in ("host", "content-length", "authorization", "transfer-encoding")
        }
        if name == QWEN and QWEN_KEY.exists():
            headers["Authorization"] = "Bearer " + QWEN_KEY.read_text(encoding="ascii").strip()
        swapper.inflight[name] += 1
        swapper.last_used[name] = time.monotonic()
        try:
            assert swapper.session is not None
            async with swapper.session.request(
                request.method,
                f"http://127.0.0.1:{BACK[name]}/{path}",
                params=request.query,
                data=body,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=None, sock_read=900),
            ) as upstream:
                response = web.StreamResponse(status=upstream.status)
                for key, value in upstream.headers.items():
                    if key.lower() not in (
                        "content-length",
                        "transfer-encoding",
                        "content-encoding",
                        "connection",
                    ):
                        response.headers[key] = value
                await response.prepare(request)
                tail = b""
                async for chunk in upstream.content.iter_any():
                    await response.write(chunk)
                    tail = (tail + chunk)[-8192:]
                await response.write_eof()
                # Only agent turns move the meter: they send tools. A title or
                # compression call without tools would read as an empty context.
                if path.endswith("chat/completions") and b'"tools"' in body:
                    used = context_used(tail)
                    if used is not None:
                        swapper.context = {
                            "profile": name,
                            "model": MODEL_ID[name],
                            "used": used,
                            "window": CTX[name],
                            "at": time.time(),
                        }
                return response
        finally:
            swapper.inflight[name] -= 1
            swapper.last_used[name] = time.monotonic()

    return handle


async def main() -> None:
    closed = await asyncio.to_thread(close_strays)
    if closed:
        log.warning("closed a duplicate model server at start: %s", closed.replace("\n", "; "))
    swapper.session = aiohttp.ClientSession()
    runners = []
    for name, port in FRONT.items():
        app = web.Application(client_max_size=256 * 1024 * 1024)
        app.router.add_route("*", "/{tail:.*}", handler_for(name))
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        # A server closed just above releases its port a moment later.
        for attempt in range(20):
            try:
                await web.TCPSite(runner, "127.0.0.1", port).start()
                break
            except OSError:
                if attempt == 19:
                    raise
                await asyncio.sleep(1.0)
        runners.append(runner)
    log.info(
        "model-swap listening: gemma %s, qwen %s, qwen3-vl %s, ui-venus %s",
        FRONT[GEMMA],
        FRONT[QWEN],
        FRONT[QWEN_VL],
        FRONT[UI_VENUS],
    )
    # Cold start: neither model is loaded until first request.
    await swapper.idle_loop()


if __name__ == "__main__":
    import atexit
    import faulthandler

    # pythonw has no console: a crash or a kill would otherwise leave no trace.
    _fault_log = open("C:/models/model-swap-fault.log", "a", encoding="utf-8")  # noqa: SIM115
    faulthandler.enable(_fault_log)
    atexit.register(lambda: log.warning("model-swap exiting"))
    if not single_instance():
        log.info("model-swap already running; this copy exits")
        raise SystemExit(0)
    try:
        asyncio.run(main())
    except BaseException:
        log.exception("model-swap stopped")
        raise
