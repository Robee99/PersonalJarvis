"""Jarvis's native control bridge, loaded in the Hermes gateway process.

The existing platform factory owns HTTP registration. No Hermes source is
modified and no model turn is used to cancel work. The registry's real child
interrupt callback is the cancellation seam. Registry context and native
compression lineage fence every request to one profile and conversation.
"""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)
_ACTIVE = {"running", "stalling"}


def register(ctx):
    ctx.register_platform_handler("api_server", _wire)


def _wire(app, adapter):
    from aiohttp import web
    from hermes_constants import get_hermes_home

    from tools import async_delegation as registry

    home = get_hermes_home().resolve()
    accepted = {}
    control_lock = asyncio.Lock()

    async def owned(request):
        # Native routes authenticate individually, not in app middleware.
        # Require a configured key even on manually wired/test listeners.
        if not adapter._expected_api_key():
            return None, web.json_response({"error": "Control key is not configured."}, status=403)
        error = adapter._check_auth(request)
        if error is not None:
            return None, error
        if get_hermes_home().resolve() != home:
            return None, web.json_response({"error": "Control profile does not match."}, status=403)
        sid = request.match_info["session_id"]
        db = await adapter._ensure_session_db_async()
        if db is None:
            return None, web.json_response({"error": "Session store unavailable."}, status=503)
        session = await asyncio.to_thread(db.get_session, sid)
        if session is None:
            return [], None  # no conversation yet, hence no owned work
        if session.get("source") != "api_server":
            return None, web.json_response(
                {"error": "This is not an API conversation."}, status=403
            )
        key, error = adapter._parse_session_key_header(request)
        if error is not None:
            return None, error
        if not key or session.get("session_key") != key:
            return None, web.json_response(
                {"error": "Conversation key does not match."}, status=403
            )

        def snapshot():
            tip = db.resolve_resume_session_id(sid)
            with registry._records_lock:
                records = list(registry._records.values())
            result = []
            for record in records:
                context = record.get("_context")
                parent = str(record.get("parent_session_id") or "")
                if not context or not parent:
                    continue
                # Session ids are only unique within a profile's state.db.
                if context.copy().run(get_hermes_home).resolve() != home:
                    continue
                if parent == sid or db.resolve_resume_session_id(parent) == tip:
                    result.append(record)
            return result

        try:
            return await asyncio.to_thread(snapshot), None
        except Exception as exc:
            log.warning("Native delegation lookup failed (%s)", type(exc).__name__)
            return None, web.json_response(
                {"error": "Delegation controls unavailable."}, status=503
            )

    def rows(records):
        # Do not export goals, prompts, results, paths, user ids or credentials.
        return [
            {
                "delegation_id": r["delegation_id"],
                "status": "interrupt_requested"
                if accepted.get(r["delegation_id"]) is r and r.get("status") in _ACTIVE
                else r.get("status", "unknown"),
            }
            for r in records
        ]

    async def roster(request):
        records, error = await owned(request)
        if error is not None:
            return error
        return web.json_response(
            {"session_id": request.match_info["session_id"], "data": rows(records)}
        )

    async def stop(request):
        async with control_lock:
            records, error = await owned(request)
            if error is not None:
                return error
            body, error = await adapter._read_json_body(request)
            if error is not None:
                return error
            ids = body.get("delegation_ids")
            if not isinstance(ids, list) or any(not isinstance(x, str) for x in ids):
                return web.json_response(
                    {"error": "delegation_ids must be a list of strings."}, status=400
                )
            targets = {r["delegation_id"]: r for r in records}
            if set(ids) - targets.keys():
                return web.json_response(
                    {"error": "Delegation is expired or outside this conversation."}, status=409
                )
            failed = []
            requested = []
            for rid in dict.fromkeys(ids):
                record = targets[rid]
                with registry._records_lock:
                    active = (
                        registry._records.get(rid) is record and record.get("status") in _ACTIVE
                    )
                if not active or accepted.get(rid) is record:
                    continue
                fn = record.get("interrupt_fn")
                try:
                    if not callable(fn):
                        raise RuntimeError("No native interrupt callback")
                    # The same callback used by native session Stop; run in its
                    # captured profile context, off the gateway's event loop.
                    await asyncio.to_thread(record["_context"].copy().run, fn)
                except Exception as exc:
                    log.warning("Native delegation interrupt failed (%s)", type(exc).__name__)
                    failed.append(rid)
                else:
                    accepted[rid] = record
                    requested.append(rid)
            # Trim acknowledgements when native records have been retired.
            with registry._records_lock:
                retired = [
                    rid
                    for rid, record in accepted.items()
                    if registry._records.get(rid) is not record
                ]
            for rid in retired:
                accepted.pop(rid)
            return web.json_response(
                {
                    "session_id": request.match_info["session_id"],
                    "requested": requested,
                    "failed": failed,
                    "data": rows(records),
                },
                status=503 if failed else 200,
            )

    path = "/api/jarvis/conversations/{session_id}/delegations"
    app.router.add_get(path, roster)
    app.router.add_post(path + "/stop", stop)
