"""Placement maths, command shape and the benchmark loop of the local LLM lab."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pytest

from scripts.local_llm_lab import (
    MIB,
    ServerProfile,
    Turn,
    build_command,
    layout_from_tensors,
    parse_nvidia_csv,
    parse_timings,
    plan_offload,
    read_layout,
    run_bench,
    summarize,
    wait_healthy,
)

GIB = 1024 * MIB


def _qwen_like(expert_gib_per_layer: float, layers: int = 40, other_gib: float = 2.0):
    tensors = [("token_embd.weight", int(other_gib * GIB))]
    for i in range(layers):
        each = int(expert_gib_per_layer * GIB / 3)
        tensors += [
            (f"blk.{i}.ffn_gate_exps.weight", each),
            (f"blk.{i}.ffn_up_exps.weight", each),
            (f"blk.{i}.ffn_down_exps.weight", each),
            (f"blk.{i}.ffn_up_shexp.weight", 1),
        ]
    return layout_from_tensors(
        "m.gguf", int((other_gib + layers * expert_gib_per_layer) * GIB), tensors
    )


def test_layout_counts_routed_experts_per_layer_and_keeps_shared_experts_dense() -> None:
    layout = layout_from_tensors(
        "m.gguf",
        100,
        [
            ("token_embd.weight", 10),
            ("blk.0.ffn_gate_up_exps.weight", 30),
            ("blk.0.ffn_down_exps.weight", 20),
            ("blk.0.ffn_down_shexp.weight", 5),
            ("blk.1.attn_q.weight", 7),
            ("blk.1.ffn_up_exps.weight", 25),
        ],
    )
    assert layout.n_layers == 2
    assert layout.expert_bytes_by_layer == (50, 25)
    assert layout.other_bytes == 22


def test_q4_on_16gb_laptop_does_not_fit_and_says_it_will_stream() -> None:
    """22.4 GB-class file, 8 GB GPU, about 11 GB free RAM: the pinned setup."""
    layout = _qwen_like(0.5)
    plan = plan_offload(
        layout,
        vram_budget_bytes=7600 * MIB,
        gpu_overhead_bytes=1536 * MIB,
        free_ram_bytes=11 * GIB,
        ram_reserve_bytes=3 * GIB,
    )
    assert 0 < plan.n_cpu_moe < 40
    assert plan.gpu_bytes <= 7600 * MIB
    assert not plan.fits_in_ram
    assert plan.load_mode == "mmap"
    assert "stream" in plan.notes[-1]


def test_q3_on_same_laptop_fits_and_drops_mmap() -> None:
    layout = _qwen_like(0.3)
    plan = plan_offload(
        layout,
        vram_budget_bytes=7600 * MIB,
        gpu_overhead_bytes=1536 * MIB,
        free_ram_bytes=12 * GIB,
        ram_reserve_bytes=2 * GIB,
    )
    assert plan.fits_in_ram
    assert plan.load_mode == "none"
    # Experts kept on the GPU are the LAST layers, matching --n-cpu-moe.
    assert plan.cpu_expert_bytes == sum(layout.expert_bytes_by_layer[: plan.n_cpu_moe])


def test_dense_part_too_big_for_vram_is_reported() -> None:
    plan = plan_offload(
        _qwen_like(0.1, other_gib=9.0),
        vram_budget_bytes=7600 * MIB,
        gpu_overhead_bytes=1536 * MIB,
        free_ram_bytes=32 * GIB,
        ram_reserve_bytes=0,
    )
    assert plan.n_cpu_moe == 40
    assert "VRAM budget" in plan.notes[0]


def test_read_layout_parses_a_real_gguf(tmp_path: Path) -> None:
    gguf = pytest.importorskip("gguf")
    path = tmp_path / "tiny.gguf"
    writer = gguf.GGUFWriter(str(path), "qwen35moe")
    writer.add_tensor("token_embd.weight", np.zeros((4, 8), dtype=np.float32))
    for i in range(2):
        writer.add_tensor(f"blk.{i}.ffn_up_exps.weight", np.zeros((2, 4, 8), dtype=np.float32))
        writer.add_tensor(f"blk.{i}.attn_q.weight", np.zeros((8, 8), dtype=np.float32))
    writer.write_header_to_file()
    writer.write_kv_data_to_file()
    writer.write_tensors_to_file()
    writer.close()

    layout = read_layout(path)

    assert layout.n_layers == 2
    assert layout.expert_bytes_by_layer == (2 * 4 * 8 * 4, 2 * 4 * 8 * 4)
    assert layout.other_bytes == (4 * 8 + 2 * 8 * 8) * 4


def test_command_places_explicitly_and_bounds_host_memory() -> None:
    cmd = build_command(
        "llama-server",
        ServerProfile(model="q.gguf", n_cpu_moe=34, mmproj="mm.gguf", mmproj_offload=False),
    )
    joined = " ".join(cmd)
    assert "-ngl all" in joined
    assert "--n-cpu-moe 34" in joined
    assert "--fit off" in joined
    assert "-np 1" in joined
    assert "--cache-ram 0" in joined
    assert "-lm none" in joined
    assert "-ctk q8_0 -ctv q8_0" in joined
    assert "--port 11435" in joined
    assert "--mmproj mm.gguf --no-mmproj-offload" in joined
    assert "--spec-type" not in joined


def test_command_defaults_to_all_experts_on_cpu_and_optional_mtp() -> None:
    cmd = build_command("llama-server", ServerProfile(model="q.gguf", spec_mtp=True))
    assert "--cpu-moe" in cmd
    assert "--n-cpu-moe" not in cmd
    assert cmd[-2:] == ["--spec-type", "draft-mtp"]


def test_nvidia_csv_rows() -> None:
    rows = parse_nvidia_csv(
        "NVIDIA GeForce RTX 4060 Laptop GPU, 591.44, 8188, 6900, 71, 2100, 80.5, 115.0, 97\n"
    )
    assert rows[0]["memory.used"] == "6900"
    assert rows[0]["temperature.gpu"] == "71"
    assert parse_nvidia_csv("garbage") == []


def test_timings_parse_and_reject() -> None:
    reply = {
        "timings": {
            "prompt_n": 40,
            "prompt_per_second": 812.5,
            "predicted_n": 256,
            "predicted_per_second": 24.1,
            "cache_n": 12,
        }
    }
    assert parse_timings(reply) == (40, 812.5, 256, 24.1, 12)
    assert parse_timings({"choices": []}) is None


def test_summary_separates_peak_sustained_and_drift() -> None:
    turns = [
        Turn(t, 64, 900.0, 256, tps, 0)
        for t, tps in [(10, 30.0), (50, 28.0), (550, 21.0), (590, 20.0)]
    ]
    samples = [
        {"temperature.gpu": "70", "ram_available_mib": 3000, "swap_used_mib": 100},
        {"temperature.gpu": "84", "ram_available_mib": 2500, "swap_used_mib": 400},
    ]
    s = summarize(turns, samples, seconds=600)
    assert s["generation_tps_peak"] == 30.0
    assert s["generation_tps_median"] == 24.5
    assert s["prompt_tps_median"] == 900.0
    assert s["first_minute_vs_last_minute_pct"] < -20
    assert s["gpu_temp_c_max"] == 84.0
    assert s["ram_available_mib_min"] == 2500
    assert s["swap_used_mib_max"] == 400


class _FakeLlama(BaseHTTPRequestHandler):
    bodies: list[dict] = []

    def log_message(self, *args) -> None:  # silence test output
        return

    def do_GET(self) -> None:
        self.send_response(200 if self.path == "/health" else 404)
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _FakeLlama.bodies.append(body)
        reply = {
            "choices": [{"message": {"content": "ok"}}],
            "timings": {
                "prompt_n": 30,
                "prompt_per_second": 700.0,
                "predicted_n": 64,
                "predicted_per_second": 25.0,
                "cache_n": 0,
            },
        }
        data = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def fake_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeLlama)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_bench_loop_against_a_server_writes_evidence(fake_server: str, tmp_path: Path) -> None:
    _FakeLlama.bodies.clear()
    assert wait_healthy(fake_server, timeout_s=5)

    summary = run_bench(fake_server, minutes=0.02, max_tokens=64, out_dir=tmp_path, label="t")

    assert summary["turns"] >= 1
    assert summary["generation_tps_median"] == 25.0
    assert summary["errors"] == 0
    # Thinking off: the benchmark measures generation speed, not reasoning length.
    assert all(b["reasoning_effort"] == "none" for b in _FakeLlama.bodies)
    written = sorted(p.name.split("-")[-1] for p in tmp_path.iterdir())
    assert written == ["samples.csv", "summary.json", "turns.csv"]


def test_bench_stops_after_repeated_failures() -> None:
    summary = run_bench(
        "http://127.0.0.1:9",
        minutes=1,
        max_tokens=8,
        out_dir=None,
        label="dead",
        request_timeout=1.0,
    )
    assert summary["turns"] == 0
    assert summary["errors"] == 3
