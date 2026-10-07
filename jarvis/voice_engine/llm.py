"""Streaming chat against a local model server, with tool calls and timings.

:class:`OllamaChat` uses Ollama's native ``/api/chat`` because it accepts the
voice profile directly (``num_ctx``, ``think: false``, ``keep_alive``) and
reports prompt and generation counters that show whether the prefix cache hit.

:class:`OpenAIChat` is the portable client for an OpenAI-compatible server
(llama-server, LM Studio, vLLM): the model, its context and its residency are
the server's, so only the turn itself is sent. Both return the same
:class:`LlmResult` and take the same Ollama-shaped history the engine keeps.
"""

from __future__ import annotations

import http.client
import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from jarvis.voice_engine.chunker import ClauseChunker

DEFAULT_BASE_URL = "http://127.0.0.1:11434"
#: ``[voice_engine] llm_api`` values: Ollama's own API, or any OpenAI-compatible one.
LLM_APIS = ("ollama", "openai")


@dataclass(slots=True)
class LlmResult:
    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    clauses: list[str] = field(default_factory=list)
    t_first_token: float | None = None  # seconds from request start
    t_first_clause: float | None = None
    t_done: float = 0.0
    prompt_tokens: int = 0
    prompt_cached_tokens: int = 0
    prompt_eval_s: float = 0.0
    eval_tokens: int = 0
    eval_s: float = 0.0
    load_s: float = 0.0
    # Reasoning text a model produced although thinking was switched off; it
    # delays the first token without being spoken, so the bench reports it.
    thinking_chars: int = 0
    error: str = ""

    @property
    def generation_tps(self) -> float:
        return self.eval_tokens / self.eval_s if self.eval_s > 0 else 0.0

    @property
    def prefill_tps(self) -> float:
        # prompt_eval_count includes the tokens served from the prefix cache
        # (measured on Ollama 0.35: 260 counted, 256 cached, 49 ms), so only
        # the remainder was actually evaluated.
        evaluated = self.prompt_tokens - self.prompt_cached_tokens
        return evaluated / self.prompt_eval_s if self.prompt_eval_s > 0 and evaluated > 0 else 0.0


class OllamaChat:
    def __init__(
        self,
        model: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        num_ctx: int = 8192,
        keep_alive: str = "30m",
        temperature: float = 0.2,
        num_gpu: int | None = None,
        timeout_s: float = 120.0,
    ) -> None:
        parts = urlsplit(base_url)
        if parts.scheme != "http" or not parts.hostname:
            raise ValueError(f"expected an http://host:port Ollama address, got {base_url!r}")
        self.model = model
        self._host = parts.hostname
        self._port = parts.port or 11434
        self._timeout = timeout_s
        self._keep_alive = keep_alive
        self._options: dict[str, Any] = {"num_ctx": num_ctx, "temperature": temperature}
        if num_gpu is not None:
            self._options["num_gpu"] = num_gpu
        self._think_supported = True

    def _post(self, path: str, payload: dict[str, Any]) -> http.client.HTTPResponse:
        connection = http.client.HTTPConnection(self._host, self._port, timeout=self._timeout)
        body = json.dumps(payload).encode("utf-8")
        connection.request("POST", path, body=body, headers={"Content-Type": "application/json"})
        return connection.getresponse()

    def unload(self) -> None:
        """Ask Ollama to drop the model now, so the next load is a cold one."""
        response = self._post("/api/generate", {"model": self.model, "keep_alive": 0})
        response.read()

    def loaded_models(self) -> list[dict[str, Any]]:
        """Ollama's view of resident models: size and how much sits in VRAM."""
        connection = http.client.HTTPConnection(self._host, self._port, timeout=self._timeout)
        connection.request("GET", "/api/ps")
        response = connection.getresponse()
        payload = json.loads(response.read() or b"{}")
        return [
            {
                "name": entry.get("name"),
                "size_gb": round(int(entry.get("size") or 0) / 1e9, 2),
                "size_vram_gb": round(int(entry.get("size_vram") or 0) / 1e9, 2),
                "context_length": entry.get("context_length"),
            }
            for entry in payload.get("models", [])
        ]

    def prime(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None) -> float:
        """Evaluate a prompt prefix now so the next turn starts from the cache.

        Hybrid models (Qwen3.5) resume only from the end of an earlier prompt,
        not from any shared prefix, so a new conversation pays the full system
        and tool prompt unless that exact prefix was evaluated before. One
        token of generation is enough to leave the checkpoint behind.
        """
        payload: dict[str, Any] = {
            "model": self.model, "messages": messages, "stream": False,
            "keep_alive": self._keep_alive, "options": {**self._options, "num_predict": 1},
        }
        if tools:
            payload["tools"] = tools
        if self._think_supported:
            payload["think"] = False
        started = time.perf_counter()
        response = self._post("/api/chat", payload)
        response.read()
        return time.perf_counter() - started

    def warm(self) -> float:
        """Load the model with the voice profile; returns seconds spent."""
        started = time.perf_counter()
        response = self._post(
            "/api/generate",
            {"model": self.model, "prompt": "", "keep_alive": self._keep_alive,
             "options": self._options},
        )
        response.read()
        if response.status != 200:
            raise RuntimeError(f"Ollama could not load {self.model}: HTTP {response.status}")
        return time.perf_counter() - started

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        chunker: ClauseChunker | None = None,
        on_clause: Callable[[str, float], None] | None = None,
        stop: threading.Event | None = None,
    ) -> LlmResult:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "keep_alive": self._keep_alive,
            "options": self._options,
        }
        if tools:
            payload["tools"] = tools
        if self._think_supported:
            payload["think"] = False
        started = time.perf_counter()
        response = self._post("/api/chat", payload)
        if response.status == 400 and self._think_supported:
            detail = response.read().decode("utf-8", "replace")
            if "think" in detail.lower():
                # Models without a reasoning mode reject the switch; they have
                # nothing to turn off, so retry once without it.
                self._think_supported = False
                return self.chat(messages, tools=tools, chunker=chunker, on_clause=on_clause,
                                 stop=stop)
            return LlmResult(
                error=f"HTTP 400: {detail[:300]}", t_done=time.perf_counter() - started
            )
        if response.status != 200:
            detail = response.read().decode("utf-8", "replace")
            return LlmResult(error=f"HTTP {response.status}: {detail[:300]}",
                             t_done=time.perf_counter() - started)
        result = LlmResult()
        chunker = chunker or ClauseChunker()
        try:
            for raw in response:
                if stop is not None and stop.is_set():
                    result.error = "cancelled"
                    break
                line = raw.strip()
                if not line:
                    continue
                event = json.loads(line)
                if event.get("error"):
                    result.error = str(event["error"])
                    break
                message = event.get("message") or {}
                delta = message.get("content") or ""
                result.thinking_chars += len(message.get("thinking") or "")
                now = time.perf_counter() - started
                if delta:
                    if result.t_first_token is None:
                        result.t_first_token = now
                    result.text += delta
                    for clause in chunker.feed(delta):
                        self._clause(result, clause, now, on_clause)
                for call in message.get("tool_calls") or []:
                    if result.t_first_token is None:
                        result.t_first_token = now
                    function = call.get("function") or {}
                    result.tool_calls.append(
                        {
                            "name": function.get("name", ""),
                            "arguments": function.get("arguments") or {},
                        }
                    )
                if event.get("done"):
                    result.prompt_tokens = int(event.get("prompt_eval_count") or 0)
                    result.prompt_cached_tokens = int(event.get("prompt_eval_cached_count") or 0)
                    result.prompt_eval_s = int(event.get("prompt_eval_duration") or 0) / 1e9
                    result.eval_tokens = int(event.get("eval_count") or 0)
                    result.eval_s = int(event.get("eval_duration") or 0) / 1e9
                    result.load_s = int(event.get("load_duration") or 0) / 1e9
        finally:
            response.close()
        end = time.perf_counter() - started
        for clause in chunker.flush():
            self._clause(result, clause, end, on_clause)
        result.t_done = end
        return result

    @staticmethod
    def _clause(
        result: LlmResult, clause: str, at: float, on_clause: Callable[[str, float], None] | None
    ) -> None:
        if result.t_first_clause is None:
            result.t_first_clause = at
        result.clauses.append(clause)
        if on_clause is not None:
            on_clause(clause, at)


class OpenAIChat:
    """Streaming ``/v1/chat/completions`` with the same surface as :class:`OllamaChat`.

    Thinking is switched off per request through ``chat_template_kwargs``
    (llama-server and vLLM pass it into the chat template; a server that does
    not know the field ignores it). llama-server's ``timings`` block, when
    present, fills the same prompt and generation counters as Ollama's.
    """

    def __init__(
        self,
        model: str,
        *,
        base_url: str,
        temperature: float = 0.2,
        timeout_s: float = 120.0,
    ) -> None:
        parts = urlsplit(base_url)
        if parts.scheme != "http" or not parts.hostname:
            raise ValueError(f"expected an http://host:port server address, got {base_url!r}")
        self.model = model
        self._host = parts.hostname
        self._port = parts.port or 80
        # The root and a pasted ``…/v1`` both work, like the brain's card.
        self._prefix = parts.path.rstrip("/").removesuffix("/v1")
        self._timeout = timeout_s
        self._temperature = temperature

    def _connection(self) -> http.client.HTTPConnection:
        return http.client.HTTPConnection(self._host, self._port, timeout=self._timeout)

    def warm(self) -> float:
        """Prove the server answers and lists a model; returns seconds spent.

        The server owns loading, so there is nothing to load from here.
        """
        started = time.perf_counter()
        connection = self._connection()
        connection.request("GET", f"{self._prefix}/v1/models")
        response = connection.getresponse()
        payload = response.read()
        if response.status != 200:
            raise RuntimeError(f"the model server answered HTTP {response.status}")
        if not (json.loads(payload or b"{}").get("data") or []):
            raise RuntimeError("the model server lists no models")
        return time.perf_counter() - started

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        chunker: ClauseChunker | None = None,
        on_clause: Callable[[str, float], None] | None = None,
        stop: threading.Event | None = None,
    ) -> LlmResult:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": openai_messages(messages),
            "stream": True,
            "stream_options": {"include_usage": True},
            "temperature": self._temperature,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        if tools:
            payload["tools"] = tools
        started = time.perf_counter()
        connection = self._connection()
        connection.request(
            "POST",
            f"{self._prefix}/v1/chat/completions",
            body=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        if response.status != 200:
            detail = response.read().decode("utf-8", "replace")
            return LlmResult(error=f"HTTP {response.status}: {detail[:300]}",
                             t_done=time.perf_counter() - started)
        result = LlmResult()
        chunker = chunker or ClauseChunker()
        calls: dict[int, dict[str, str]] = {}
        try:
            for raw in response:
                if stop is not None and stop.is_set():
                    result.error = "cancelled"
                    break
                line = raw.strip()
                if not line.startswith(b"data:"):
                    continue
                data = line[5:].strip()
                if data == b"[DONE]":
                    break
                event = json.loads(data)
                if event.get("error"):
                    result.error = str(event["error"])
                    break
                now = time.perf_counter() - started
                for choice in event.get("choices") or []:
                    delta = choice.get("delta") or {}
                    result.thinking_chars += len(delta.get("reasoning_content") or "")
                    text = delta.get("content") or ""
                    if text:
                        if result.t_first_token is None:
                            result.t_first_token = now
                        result.text += text
                        for clause in chunker.feed(text):
                            self._clause(result, clause, now, on_clause)
                    for call in delta.get("tool_calls") or []:
                        if result.t_first_token is None:
                            result.t_first_token = now
                        slot = calls.setdefault(int(call.get("index") or 0),
                                                {"name": "", "arguments": ""})
                        function = call.get("function") or {}
                        slot["name"] += function.get("name") or ""
                        slot["arguments"] += function.get("arguments") or ""
                _read_counters(result, event)
        finally:
            response.close()
        for index in sorted(calls):
            name, arguments = calls[index]["name"], calls[index]["arguments"]
            try:
                parsed = json.loads(arguments) if arguments.strip() else {}
            except ValueError:
                # A truncated argument string is still a call; the tool says
                # what it is missing instead of the turn failing here.
                parsed = {}
            result.tool_calls.append(
                {"name": name, "arguments": parsed if isinstance(parsed, dict) else {}}
            )
        end = time.perf_counter() - started
        for clause in chunker.flush():
            self._clause(result, clause, end, on_clause)
        result.t_done = end
        return result

    _clause = staticmethod(OllamaChat._clause)


def _read_counters(result: LlmResult, event: dict[str, Any]) -> None:
    """Prompt and generation counters from llama-server ``timings``, else ``usage``."""
    timings = event.get("timings")
    if isinstance(timings, dict):
        cached = int(timings.get("cache_n") or 0)
        result.prompt_cached_tokens = cached
        result.prompt_tokens = int(timings.get("prompt_n") or 0) + cached
        result.prompt_eval_s = float(timings.get("prompt_ms") or 0.0) / 1000.0
        result.eval_tokens = int(timings.get("predicted_n") or 0)
        result.eval_s = float(timings.get("predicted_ms") or 0.0) / 1000.0
        return
    usage = event.get("usage")
    if isinstance(usage, dict) and not result.prompt_tokens:
        result.prompt_tokens = int(usage.get("prompt_tokens") or 0)
        result.eval_tokens = int(usage.get("completion_tokens") or 0)


def openai_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The engine's Ollama-shaped history in the OpenAI chat shape.

    Ollama pairs a tool result with its call by order and name and takes the
    arguments as an object; the OpenAI shape pairs them by ``tool_call_id``
    and takes the arguments as a JSON string. Ids are given here, in order.
    """
    converted: list[dict[str, Any]] = []
    pending: list[str] = []
    for index, message in enumerate(messages):
        role = message.get("role")
        if role == "assistant" and message.get("tool_calls"):
            calls = []
            for number, call in enumerate(message["tool_calls"]):
                function = call.get("function") or {}
                arguments = function.get("arguments")
                call_id = f"call_{index}_{number}"
                pending.append(call_id)
                calls.append({
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": function.get("name", ""),
                        "arguments": arguments if isinstance(arguments, str)
                        else json.dumps(arguments or {}, ensure_ascii=False),
                    },
                })
            converted.append(
                {"role": "assistant", "content": message.get("content") or "", "tool_calls": calls}
            )
        elif role == "tool":
            call_id = pending.pop(0) if pending else f"call_{index}"
            converted.append(
                {"role": "tool", "tool_call_id": call_id, "content": message.get("content") or ""}
            )
        else:
            converted.append({"role": role, "content": message.get("content") or ""})
    return converted


def make_chat(
    api: str,
    model: str,
    *,
    base_url: str,
    num_ctx: int = 8192,
    keep_alive: str = "30m",
    temperature: float = 0.2,
) -> OllamaChat | OpenAIChat:
    """The client for ``[voice_engine] llm_api``; anything unknown is Ollama."""
    if api == "openai":
        return OpenAIChat(model, base_url=base_url, temperature=temperature)
    return OllamaChat(model, base_url=base_url, num_ctx=num_ctx, keep_alive=keep_alive,
                      temperature=temperature)
