"""Turn one non-Markdown source file into wiki pages for the importer.

Two kinds of input, both read through code the repository already has:

* **Documents** (PDF, Word, PowerPoint, Excel, OpenDocument, EPUB, RTF, HTML,
  CSV, JSON, code, plain text): ``jarvis.documents.extract.extract_text``
  gives the text; it becomes one page titled after the file.
* **AI conversation exports**: ChatGPT's and Claude's ``conversations.json``,
  a single conversation object, and JSON Lines datasets of ``messages`` (or
  prompt/response) records. Each conversation becomes its own page with its
  title and its turns, so a chat history shows up in the memory orb and in
  recall as conversations, not as one wall of JSON.

Nothing here executes content: JSON is parsed as data, documents are read as
text. Pages are plain Markdown strings; the importer decides where they go and
applies the secret guard.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

#: A converted document bigger than this is cut, with a note on the page.
MAX_PAGE_CHARS: Final[int] = 1_000_000

#: Conversation records per page for a JSON Lines dataset.
DATASET_RECORDS_PER_PAGE: Final[int] = 25

#: Why a file gave no page, in the words the import report shows.
NO_TEXT_REASONS: Final[dict[str, str]] = {
    "image": "picture (no text)",
    "audio": "recording (no text)",
    "video": "video (no text)",
    "archive": "archive (unzip it first)",
    "tar": "archive (unzip it first)",
    "gzip": "archive (unzip it first)",
    "legacy_office": "old Office format (save it as .docx/.xlsx/.pptx)",
    "binary": "binary file",
    "empty": "empty",
}


@dataclass(frozen=True, slots=True)
class Page:
    """One page to write: its name (no extension) and its Markdown body."""

    name: str
    body: str


class NoText(ValueError):
    """The file holds nothing that becomes a page; ``str(exc)`` says why."""


# --- conversations ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Conversation:
    title: str
    created: str
    turns: tuple[tuple[str, str], ...]


_ROLE_NAMES: Final[dict[str, str]] = {
    "user": "You",
    "human": "You",
    "assistant": "Assistant",
    "model": "Assistant",
    "gpt": "Assistant",
    "bot": "Assistant",
    "system": "System",
    "tool": "Tool",
}


def _text_of(content: Any) -> str:
    """Text out of the content shapes chat exports use (str, parts, blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        if isinstance(content.get("parts"), list):
            return _text_of(content["parts"])
        for key in ("text", "content", "value"):
            if key in content:
                return _text_of(content[key])
        return ""
    if isinstance(content, list):
        return "\n".join(t for t in (_text_of(item) for item in content) if t.strip())
    return ""


def _when(value: Any) -> str:
    if isinstance(value, (int, float)) and value > 0:
        return datetime.fromtimestamp(float(value), tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
    if isinstance(value, str) and value.strip():
        return value.strip()[:25]
    return ""


def _turn(role: Any, content: Any) -> tuple[str, str] | None:
    text = _text_of(content).strip()
    if not text:
        return None
    return _ROLE_NAMES.get(str(role or "").lower(), str(role or "Unknown").title()), text


def _chatgpt(item: dict[str, Any]) -> Conversation | None:
    """ChatGPT export: a message tree in ``mapping``; follow ``current_node`` up."""
    mapping = item.get("mapping")
    if not isinstance(mapping, dict):
        return None
    node_id = item.get("current_node")
    chain: list[dict[str, Any]] = []
    seen: set[str] = set()
    while isinstance(node_id, str) and node_id in mapping and node_id not in seen:
        seen.add(node_id)
        node = mapping[node_id] or {}
        chain.append(node)
        node_id = node.get("parent")
    if not chain:  # no current_node: take the nodes as stored
        chain = [n for n in mapping.values() if isinstance(n, dict)][::-1]
    turns = []
    for node in reversed(chain):
        message = node.get("message") or {}
        role = (message.get("author") or {}).get("role")
        if role not in ("user", "assistant"):
            continue
        turn = _turn(role, message.get("content"))
        if turn:
            turns.append(turn)
    return Conversation(
        title=str(item.get("title") or "Untitled conversation"),
        created=_when(item.get("create_time")),
        turns=tuple(turns),
    )


def _claude(item: dict[str, Any]) -> Conversation | None:
    """Claude export: ``chat_messages`` with ``sender`` and ``text``/``content``."""
    messages = item.get("chat_messages")
    if not isinstance(messages, list):
        return None
    turns = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("text") or message.get("content")
        turn = _turn(message.get("sender"), content)
        if turn:
            turns.append(turn)
    return Conversation(
        title=str(item.get("name") or "Untitled conversation"),
        created=_when(item.get("created_at")),
        turns=tuple(turns),
    )


def _messages(item: dict[str, Any]) -> Conversation | None:
    """``{"messages": [{"role", "content"}]}`` (OpenAI chat datasets, ShareGPT)."""
    messages = item.get("messages", item.get("conversations"))
    if isinstance(messages, list):
        turns = []
        for message in messages:
            if isinstance(message, dict):
                turn = _turn(
                    message.get("role", message.get("from")),
                    message.get("content", message.get("value")),
                )
                if turn:
                    turns.append(turn)
        title = item.get("title") or item.get("name") or (turns[0][1][:60] if turns else "")
        return Conversation(str(title or "Conversation"), _when(item.get("created")), tuple(turns))
    prompt = item.get("prompt", item.get("instruction", item.get("question")))
    answer = item.get("completion", item.get("response", item.get("output", item.get("answer"))))
    if isinstance(prompt, str) and isinstance(answer, str):
        turns = tuple(t for t in (_turn("user", prompt), _turn("assistant", answer)) if t)
        return Conversation(prompt.strip()[:60] or "Conversation", "", turns)
    return None


def conversation_of(item: Any) -> Conversation | None:
    if not isinstance(item, dict):
        return None
    for reader in (_chatgpt, _claude, _messages):
        found = reader(item)
        if found is not None and found.turns:
            return found
    return None


def render_conversation(conv: Conversation, source_name: str) -> str:
    lines = [f"# {conv.title.strip() or 'Conversation'}", ""]
    meta = f"_Conversation imported from `{source_name}`"
    meta += f", started {conv.created}_" if conv.created else "_"
    lines += [meta, ""]
    for role, text in conv.turns:
        lines += [f"**{role}:**", "", text.strip(), ""]
    return "\n".join(lines).rstrip() + "\n"


def _unique(names: Iterable[str]) -> Iterator[str]:
    used: dict[str, int] = {}
    for name in names:
        base = " ".join(name.split())[:80].strip() or "Conversation"
        count = used.get(base.lower(), 0) + 1
        used[base.lower()] = count
        yield base if count == 1 else f"{base} ({count})"


def conversation_pages(path: Path, raw: bytes) -> list[Page] | None:
    """Pages for a chat export or dataset, or ``None`` if it is not one."""
    text = raw.decode("utf-8-sig", errors="replace")
    suffix = path.suffix.lower()
    if suffix in (".jsonl", ".ndjson"):
        records = []
        for line in text.splitlines():
            if line.strip():
                try:
                    records.append(json.loads(line))
                except ValueError:  # one bad line: the rest of the dataset still counts
                    continue
        convs = [c for c in (conversation_of(r) for r in records) if c is not None]
        if not convs or len(convs) < len(records) / 2:
            return None
        pages = []
        for start in range(0, len(convs), DATASET_RECORDS_PER_PAGE):
            chunk = convs[start : start + DATASET_RECORDS_PER_PAGE]
            first, last = start + 1, start + len(chunk)
            body = "\n\n---\n\n".join(render_conversation(c, path.name) for c in chunk)
            title = f"# {path.stem} — conversations {first} to {last}\n\n"
            pages.append(Page(f"{path.stem} {first:05d}-{last:05d}", title + body))
        return pages
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):  # not JSON at all: the document path reads it
        return None
    items = data if isinstance(data, list) else [data]
    convs = [c for c in (conversation_of(i) for i in items) if c is not None]
    if not convs:
        return None
    names = _unique(c.title for c in convs)
    pairs = zip(names, convs, strict=True)
    return [Page(name, render_conversation(c, path.name)) for name, c in pairs]


# --- documents -------------------------------------------------------------


def document_page(path: Path) -> Page:
    """One page with a document's text; raises :class:`NoText` with the reason."""
    from jarvis.documents.extract import extract_text

    result = extract_text(path, filename=path.name)
    if not result.ok or not result.text.strip():
        reason = NO_TEXT_REASONS.get(result.kind) or result.reason or "no readable text"
        raise NoText(reason)
    text = result.text.strip()
    note = ""
    if len(text) > MAX_PAGE_CHARS:
        text = text[:MAX_PAGE_CHARS]
        note = "\n\n_The rest of this file was cut: it is longer than one page holds._"
    fence = "```" if result.kind in ("json", "text") and path.suffix.lower() not in (
        ".txt", ".text", ".log", ".srt", ".vtt", ".rst", ".org", ".adoc", ".tex"
    ) else ""
    if fence:
        while fence in text:
            fence += "`"
        text = f"{fence}\n{text}\n{fence}"
    header = f"# {path.stem}\n\n_Imported from `{path.name}`_\n\n"
    return Page(path.stem, header + text + note + "\n")
