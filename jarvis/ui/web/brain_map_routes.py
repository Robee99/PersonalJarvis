"""REST API for the brain map — the project intelligence layer the Orb draws.

Endpoints (mounted by the WebServer; ``jarvis api brain-map <op>`` on the CLI):

    GET  /api/brain-map/projects            → every project, highest priority first,
                                              each with its health, plus the health
                                              rule table the colours come from
    GET  /api/brain-map/projects/{id}       → one project: summary, state, health,
                                              timeline, decisions, tasks, blockers,
                                              files, branches, agents, reports, next
                                              actions, priority, and the conversation
                                              thread as a branching tree
    GET  /api/brain-map/graph               → nodes + edges with status colours
    GET  /api/brain-map/entities/{id}       → one entity with its evidence and the
                                              jump to its source message
    POST /api/brain-map/reindex             → one incremental pass, now

Every handler is a plain ``def``: FastAPI runs it in the threadpool, so the
SQLite work never sits on the event loop (``check_async_routes.py``). The
service opens its database on the first request, never at boot (AP-26).
Reindexing through this route runs the deterministic rules only; it calls no
model and bills nothing.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from jarvis.memory.brain_map import query
from jarvis.memory.brain_map.service import BrainMapBusy, BrainMapService, shared_service

router = APIRouter(prefix="/api/brain-map", tags=["brain-map"])

# Five-layer vocabulary (AP-4): mirrors jarvis/memory/brain_map/constants.py and
# the CHECK lists of migration 0010; tests/unit/memory/brain_map/test_parity.py.
EntityKindLiteral = Literal[
    "PROJECT",
    "CONVERSATION",
    "THREAD",
    "TASK",
    "DECISION",
    "BLOCKER",
    "MILESTONE",
    "DOCUMENT",
    "AGENT",
    "OUTPUT",
]
StatusLiteral = Literal["red", "blue", "yellow", "green"]
RelationKindLiteral = Literal[
    "part_of", "mentions", "blocks", "decided_in", "produced", "follows_from"
]
AssignmentLiteral = Literal["confirmed", "suggested"]
OriginLiteral = Literal["native", "extracted", "derived"]
# Mirrors jarvis/memory/brain_map/health.py and thread.py; the parity test is
# tests/unit/memory/brain_map/test_health.py (Python, routes and the Orb's TS).
HealthRuleLiteral = Literal[
    "blocked", "failed", "verified", "stalled", "partial", "review", "active", "idle"
]
ThreadBranchLiteral = Literal[
    "topic", "decision", "experiment", "subtask", "blocker", "milestone", "artifact"
]


class SourceRef(BaseModel):
    """Where the source row lives: its store, its id, and a route to open it."""

    source_kind: str
    source_id: str
    conversation_id: str = ""
    api: str | None = None
    locator: str = ""


class EvidenceSource(SourceRef):
    message_ref: str = ""
    message_id: str = ""
    ts_ms: int = 0


class EntityOut(BaseModel):
    id: str
    kind: EntityKindLiteral
    title: str
    status: StatusLiteral
    status_reason: str
    confidence: float = Field(ge=0.0, le=1.0)
    origin: OriginLiteral
    last_activity_ms: int
    last_updated_ms: int
    priority_score: float
    priority_reason: str
    assignment: AssignmentLiteral = "confirmed"
    source: SourceRef


class ProjectCounts(BaseModel):
    conversations: int
    open_tasks: int
    open_blockers: int
    decisions: int
    suggested: int


class HealthOut(BaseModel):
    """The project's colour from ``health.HEALTH_RULES``, with the rule that fired."""

    colour: StatusLiteral
    rule: HealthRuleLiteral
    reason: str
    progress: float | None = Field(default=None, ge=0.0, le=1.0)
    done: int
    total: int
    open_blockers: int
    open_tasks: int


class HealthRuleOut(BaseModel):
    id: HealthRuleLiteral
    colour: StatusLiteral
    when: str


class ProjectOut(EntityOut):
    summary: str
    counts: ProjectCounts
    locator: str = ""
    health: HealthOut


class RunOut(BaseModel):
    run_id: int
    trigger: str
    started_ms: int
    finished_ms: int
    sources_seen: int
    sources_changed: int
    segments_seen: int
    segments_processed: int
    entities_changed: int
    projects_recomputed: int
    errors: int


class ProjectsOut(BaseModel):
    projects: list[ProjectOut]
    last_run: RunOut | None = None
    health_rules: list[HealthRuleOut]


class StateOut(BaseModel):
    status: StatusLiteral
    status_reason: str
    confidence: float
    last_updated_ms: int
    last_activity_ms: int


class TimelineItem(BaseModel):
    ts_ms: int
    entity_id: str
    kind: EntityKindLiteral
    title: str
    status: StatusLiteral | None = None
    reason: str
    event: Literal["status", "said"]


class FileRef(BaseModel):
    kind: str
    path: str
    entity_id: str | None = None


class BranchRef(BaseModel):
    name: str
    path: str
    source: str


class NextAction(BaseModel):
    action: str
    entity_id: str
    why: str


class PriorityOut(BaseModel):
    score: float
    reason: str


class ThreadBranchOut(BaseModel):
    id: str
    kind: EntityKindLiteral
    branch: ThreadBranchLiteral
    title: str
    status: StatusLiteral
    status_reason: str
    assignment: AssignmentLiteral
    first_seen_ms: int
    last_activity_ms: int
    source: SourceRef
    children: list[ThreadBranchOut]
    hidden_children: int


class ThreadOut(BaseModel):
    project_id: str
    branches: list[ThreadBranchOut]
    hidden_branches: int
    counts: dict[ThreadBranchLiteral, int]


class ProjectDetailOut(ProjectOut):
    state: StateOut
    timeline: list[TimelineItem]
    conversations: list[EntityOut]
    decisions: list[EntityOut]
    tasks: list[EntityOut]
    blockers: list[EntityOut]
    milestones: list[EntityOut]
    files: list[FileRef]
    branches: list[BranchRef]
    agents: list[EntityOut]
    reports: list[EntityOut]
    next_actions: list[NextAction]
    priority: PriorityOut
    thread: ThreadOut


class GraphNode(EntityOut):
    color: str
    suggested: bool
    health: HealthOut | None = None


class GraphEdge(BaseModel):
    source: str
    target: str
    kind: RelationKindLiteral
    confidence: float
    assignment: AssignmentLiteral


class GraphOut(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    status_colours: dict[str, str]
    health_rules: list[HealthRuleOut]


class EvidenceOut(BaseModel):
    quote: str
    char_start: int
    char_end: int
    confidence: float
    extractor: str
    segment_id: str
    source: EvidenceSource


class RelationOut(BaseModel):
    kind: RelationKindLiteral
    target: str | None = None
    source: str | None = None
    confidence: float
    assignment: AssignmentLiteral
    basis: str


class RelationsOut(BaseModel):
    outgoing: list[RelationOut]
    incoming: list[RelationOut]


class HistoryItem(BaseModel):
    ts_ms: int
    status: StatusLiteral
    reason: str


class EntityDetailOut(EntityOut):
    evidence: list[EvidenceOut]
    relations: RelationsOut
    status_history: list[HistoryItem]
    projects: list[str]
    summary: str = ""


class ReindexOut(BaseModel):
    trigger: str
    sources_seen: int
    sources_changed: int
    segments_seen: int
    segments_processed: int
    entities_changed: int
    projects_recomputed: int
    errors: int
    duration_ms: int


def _service(request: Request) -> BrainMapService:
    service = getattr(request.app.state, "brain_map", None)
    return service if isinstance(service, BrainMapService) else shared_service()


@router.get(
    "/projects", response_model=ProjectsOut, summary="Every project, highest priority first"
)
def list_brain_map_projects(request: Request) -> dict[str, Any]:
    service = _service(request)
    return {
        "projects": query.list_projects(service.store),
        "last_run": service.last_run(),
        "health_rules": query.health_rules(),
    }


@router.get(
    "/projects/{project_id}",
    response_model=ProjectDetailOut,
    summary="One project: state, timeline, decisions, tasks, blockers, next actions",
)
def get_brain_map_project(project_id: str, request: Request) -> dict[str, Any]:
    detail = query.project_detail(_service(request).store, project_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="project not found")
    return detail


@router.get("/graph", response_model=GraphOut, summary="Nodes and edges with status colours")
def get_brain_map_graph(request: Request, limit: int = Query(600, ge=1, le=5000)) -> dict[str, Any]:
    return query.graph(_service(request).store, limit=limit)


@router.get(
    "/entities/{entity_id}",
    response_model=EntityDetailOut,
    summary="One entity with its evidence and source jump info",
)
def get_brain_map_entity(entity_id: str, request: Request) -> dict[str, Any]:
    detail = query.entity_detail(_service(request).store, entity_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="entity not found")
    return detail


@router.post("/reindex", response_model=ReindexOut, summary="Run one incremental index pass now")
def reindex_brain_map(request: Request) -> dict[str, Any]:
    try:
        stats = _service(request).reindex(trigger="api")
    except BrainMapBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return stats.to_dict()
