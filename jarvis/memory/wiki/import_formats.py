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

import hashlib
import json
import re
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
class Turn:
    """One message of an imported conversation, with its provenance.

    ``message_id`` and ``ts_ms`` are the export's own values when it has them
    (ChatGPT node/message ids and ``create_time``, Claude ``uuid`` and
    ``created_at``); empty / ``None`` otherwise. They are written into the page
    as an HTML comment so the brain map can point a derived fact back at the
    exact message it came from (``jarvis.memory.brain_map``).
    """

    role: str
    text: str
    message_id: str = ""
    ts_ms: int | None = None


@dataclass(frozen=True, slots=True)
class Conversation:
    title: str
    created: str
    turns: tuple[Turn, ...]
    #: The export's conversation id, or a stable content-derived one
    #: (``derived-<hash>``) when the export has none.
    conversation_id: str = ""
    created_ms: int | None = None
    #: Which export shape it came from: ``chatgpt`` / ``claude`` / ``messages``.
    source_format: str = ""


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


def _epoch_ms(value: Any) -> int | None:
    """Milliseconds since the epoch from an export timestamp, or ``None``.

    Exports use epoch seconds (ChatGPT ``create_time``), epoch milliseconds, or
    ISO-8601 strings (Claude ``created_at``). Anything else is no timestamp.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value > 0:
        number = float(value)
        # Above ~year 33658 in seconds means the export already wrote ms.
        return int(number if number > 1e12 else number * 1000)
    if isinstance(value, str) and value.strip():
        raw = value.strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:  # free-form date text: kept as the display string only
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return int(parsed.timestamp() * 1000)
    return None


#: What an id may contain once it is written into an HTML comment on the page.
_ID_UNSAFE = re.compile(r"[^A-Za-z0-9_.:-]")


def _clean_id(value: Any) -> str:
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        return ""
    return _ID_UNSAFE.sub("", str(value))[:128]


def _turn(
    role: Any, content: Any, *, message_id: Any = "", when: Any = None
) -> Turn | None:
    text = _text_of(content).strip()
    if not text:
        return None
    name = _ROLE_NAMES.get(str(role or "").lower(), str(role or "Unknown").title())
    return Turn(name, text, _clean_id(message_id), _epoch_ms(when))


def _conversation_id(item: dict[str, Any], keys: tuple[str, ...], turns: list[Turn]) -> str:
    """The export's id, or a stable id derived from the conversation's content."""
    for key in keys:
        found = _clean_id(item.get(key))
        if found:
            return found
    digest = hashlib.sha256()
    for turn in turns[:4]:
        digest.update(turn.role.encode("utf-8"))
        digest.update(b"\0")
        digest.update(turn.text.encode("utf-8"))
        digest.update(b"\0")
    return f"derived-{digest.hexdigest()[:20]}"


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
    turns: list[Turn] = []
    for node in reversed(chain):
        message = node.get("message") or {}
        role = (message.get("author") or {}).get("role")
        if role not in ("user", "assistant"):
            continue
        turn = _turn(
            role,
            message.get("content"),
            message_id=message.get("id") or node.get("id"),
            when=message.get("create_time"),
        )
        if turn:
            turns.append(turn)
    return Conversation(
        title=str(item.get("title") or "Untitled conversation"),
        created=_when(item.get("create_time")),
        turns=tuple(turns),
        conversation_id=_conversation_id(item, ("conversation_id", "id"), turns),
        created_ms=_epoch_ms(item.get("create_time")),
        source_format="chatgpt",
    )


def _claude(item: dict[str, Any]) -> Conversation | None:
    """Claude export: ``chat_messages`` with ``sender`` and ``text``/``content``."""
    messages = item.get("chat_messages")
    if not isinstance(messages, list):
        return None
    turns: list[Turn] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("text") or message.get("content")
        turn = _turn(
            message.get("sender"),
            content,
            message_id=message.get("uuid") or message.get("id"),
            when=message.get("created_at"),
        )
        if turn:
            turns.append(turn)
    return Conversation(
        title=str(item.get("name") or "Untitled conversation"),
        created=_when(item.get("created_at")),
        turns=tuple(turns),
        conversation_id=_conversation_id(item, ("uuid", "id"), turns),
        created_ms=_epoch_ms(item.get("created_at")),
        source_format="claude",
    )


def _messages(item: dict[str, Any]) -> Conversation | None:
    """``{"messages": [{"role", "content"}]}`` (OpenAI chat datasets, ShareGPT)."""
    messages = item.get("messages", item.get("conversations"))
    if isinstance(messages, list):
        turns: list[Turn] = []
        for message in messages:
            if isinstance(message, dict):
                turn = _turn(
                    message.get("role", message.get("from")),
                    message.get("content", message.get("value")),
                    message_id=message.get("id"),
                    when=message.get("timestamp", message.get("created_at")),
                )
                if turn:
                    turns.append(turn)
        title = item.get("title") or item.get("name") or (turns[0].text[:60] if turns else "")
        return Conversation(
            str(title or "Conversation"),
            _when(item.get("created")),
            tuple(turns),
            conversation_id=_conversation_id(item, ("id", "conversation_id"), turns),
            created_ms=_epoch_ms(item.get("created")),
            source_format="messages",
        )
    prompt = item.get("prompt", item.get("instruction", item.get("question")))
    answer = item.get("completion", item.get("response", item.get("output", item.get("answer"))))
    if isinstance(prompt, str) and isinstance(answer, str):
        pair = [t for t in (_turn("user", prompt), _turn("assistant", answer)) if t]
        return Conversation(
            prompt.strip()[:60] or "Conversation",
            "",
            tuple(pair),
            conversation_id=_conversation_id(item, ("id",), pair),
            source_format="messages",
        )
    return None


def conversation_of(item: Any) -> Conversation | None:
    if not isinstance(item, dict):
        return None
    for reader in (_chatgpt, _claude, _messages):
        found = reader(item)
        if found is not None and found.turns:
            return found
    return None


#: Provenance markers written into a converted page. HTML comments: invisible
#: in Obsidian's reading view, kept verbatim by the importer, and parsed back by
#: ``parse_conversation_markers`` (the brain map's wiki-import source).
CONVERSATION_MARKER: Final[str] = "jarvis-conversation"
TURN_MARKER: Final[str] = "jarvis-turn"
_MARKER_RE = re.compile(
    r"<!-- (?P<kind>jarvis-conversation|jarvis-turn)(?P<attrs>(?: [a-z_]+=\"[^\"]*\")*) -->"
)
_ATTR_RE = re.compile(r'([a-z_]+)="([^"]*)"')


def _marker(kind: str, **attrs: Any) -> str:
    parts = [
        f'{key}="{_clean_id(value)}"'
        for key, value in attrs.items()
        if value not in (None, "")
    ]
    return f"<!-- {kind}{(' ' + ' '.join(parts)) if parts else ''} -->"


def render_conversation(conv: Conversation, source_name: str) -> str:
    lines = [f"# {conv.title.strip() or 'Conversation'}", ""]
    lines += [
        _marker(
            CONVERSATION_MARKER,
            id=conv.conversation_id,
            format=conv.source_format,
            created_ms=conv.created_ms,
        )
    ]
    meta = f"_Conversation imported from `{source_name}`"
    meta += f", started {conv.created}_" if conv.created else "_"
    lines += [meta, ""]
    for turn in conv.turns:
        lines += [
            _marker(TURN_MARKER, id=turn.message_id, ts_ms=turn.ts_ms),
            f"**{turn.role}:**",
            "",
            turn.text.strip(),
            "",
        ]
    return "\n".join(lines).rstrip() + "\n"


@dataclass(frozen=True, slots=True)
class ParsedTurn:
    """One turn read back from a converted page (see ``parse_conversation_markers``)."""

    conversation_id: str
    conversation_created_ms: int | None
    title: str
    index: int
    role: str
    text: str
    message_id: str
    ts_ms: int | None


def _int_or_none(value: str | None) -> int | None:
    try:
        return int(value) if value else None
    except ValueError:  # a hand-edited marker: no timestamp rather than a crash
        return None


def parse_conversation_markers(page_text: str) -> list[ParsedTurn]:
    """Read the conversations and turns back out of a converted page.

    Pages written before the markers existed yield nothing; the caller then
    treats the page as one undifferentiated document. Text is returned as
    written on the page, which is the redacted copy the importer stored.
    """
    out: list[ParsedTurn] = []
    matches = list(_MARKER_RE.finditer(page_text))
    conv_id = ""
    conv_created: int | None = None
    title = ""
    index = 0
    for pos, match in enumerate(matches):
        attrs = dict(_ATTR_RE.findall(match.group("attrs") or ""))
        if match.group("kind") == CONVERSATION_MARKER:
            conv_id = attrs.get("id", "")
            conv_created = _int_or_none(attrs.get("created_ms"))
            head = page_text[: match.start()].rstrip().splitlines()
            title = head[-1].lstrip("# ").strip() if head and head[-1].startswith("# ") else ""
            index = 0
            continue
        nxt = matches[pos + 1] if pos + 1 < len(matches) else None
        block = page_text[match.end() : nxt.start() if nxt else len(page_text)]
        if nxt is not None and nxt.group("kind") == CONVERSATION_MARKER:
            # A dataset page: the separator and the next conversation's heading
            # sit between this turn and the next marker; they are not its text.
            cut = block.rfind("\n---\n")
            if cut >= 0:
                block = block[:cut]
        role_match = re.match(r"\s*\*\*(?P<role>[^*\n]+):\*\*\s*\n", block)
        if role_match is None or not conv_id:
            continue
        text = block[role_match.end() :].strip()
        if not text:
            continue
        out.append(
            ParsedTurn(
                conversation_id=conv_id,
                conversation_created_ms=conv_created,
                title=title,
                index=index,
                role=role_match.group("role").strip(),
                text=text,
                message_id=attrs.get("id", ""),
                ts_ms=_int_or_none(attrs.get("ts_ms")),
            )
        )
        index += 1
    return out


#: Speaker labels the importer wrote before the markers existed. Only these
#: start a turn, so a bold "**Note:**" line inside a message stays text.
_LEGACY_ROLES: Final[frozenset[str]] = frozenset(
    {
        "you", "user", "human", "chatgpt", "assistant", "claude",
        "gemini", "ai", "model", "system", "tool",
    }
)
_LEGACY_META_RE = re.compile(
    r"^_Conversation imported from `[^`]*`"
    r"(?:, started (?P<started>\d{4}-\d{2}-\d{2} \d{2}:\d{2}) UTC)?_\s*$",
    re.MULTILINE,
)
_LEGACY_ROLE_RE = re.compile(r"^\*\*(?P<role>[^*\n]{1,40}):\*\*[ \t]*$", re.MULTILINE)


def parse_legacy_conversation_page(page_text: str, conversation_id: str) -> list[ParsedTurn]:
    """Turns of a one-conversation page written before the markers existed.

    Those pages carry the same "_Conversation imported from ..._" line and
    "**You:**" / "**ChatGPT:**" speaker lines as today's, just no ids or
    timestamps. The caller supplies a stable ``conversation_id`` (the page path
    is the only identity such a page has). Anything else yields nothing.
    """
    meta = _LEGACY_META_RE.search(page_text)
    if meta is None or _MARKER_RE.search(page_text):
        return []
    head = page_text[: meta.start()].rstrip().splitlines()
    title = head[-1].lstrip("# ").strip() if head and head[-1].startswith("# ") else ""
    created: int | None = None
    if meta.group("started"):
        started = datetime.strptime(meta.group("started"), "%Y-%m-%d %H:%M").replace(tzinfo=UTC)
        created = int(started.timestamp() * 1000)
    roles = [
        m for m in _LEGACY_ROLE_RE.finditer(page_text, meta.end())
        if m.group("role").strip().lower() in _LEGACY_ROLES
    ]
    out: list[ParsedTurn] = []
    for pos, match in enumerate(roles):
        end = roles[pos + 1].start() if pos + 1 < len(roles) else len(page_text)
        text = page_text[match.end() : end].strip()
        if not text:
            continue
        out.append(
            ParsedTurn(
                conversation_id=conversation_id,
                conversation_created_ms=created,
                title=title,
                index=len(out),
                role=match.group("role").strip(),
                text=text,
                message_id=f"{conversation_id}-{len(out)}",
                ts_ms=None,
            )
        )
    return out


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
