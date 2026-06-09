"""
runner.py
----------
Interactive CLI entry point for the Autonomous Rerouting Engine.

On launch shows a menu:
  [1] New rerouting session  — asks for vessel details, fires the agent,
                               then enters the chat loop
  [2] Resume previous session — lists recent sessions from MongoDB,
                                loads the conversation history,
                                enters the chat loop directly
  [3] Exit

The chat loop keeps the Runner, toolsets, and session alive across turns.
Every turn saves the full session state to MongoDB.
The user can argue about compliance decisions, ask for cost comparisons,
or explore hypotheticals — the agent has full conversation memory.
"""

import os, sys
import uuid
import asyncio
import json
import logging
from datetime import datetime, timezone

# ── FIX: Windows Subprocess Pipe Crash ────────────────────────────────────────
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
# ──────────────────────────────────────────────────────────────────────────────

# ── Vertex AI monkey-patch — must happen before any google.adk import ─────────
from google import genai
from config import GCP_PROJECT, GCP_REGION, MONGO_URI, MONGO_DB_NAME

_original_client_init = genai.Client.__init__
def _vertex_client_init(self, *args, **kwargs):
    kwargs["vertexai"] = True
    kwargs["project"]  = GCP_PROJECT
    kwargs["location"] = GCP_REGION
    _original_client_init(self, *args, **kwargs)
genai.Client.__init__ = _vertex_client_init
# ─────────────────────────────────────────────────────────────────────────────

import vertexai
from pymongo import MongoClient
from google.adk.runners import Runner
from google.adk import Event
from google.genai import types as genai_types

from agents.root_agent import build_root_agent
from toolsets.connections import make_mongo_toolset
from agents.trigger_agent import build_trigger_agent
from session_service import InMemorySessionService

vertexai.init(project=GCP_PROJECT, location=GCP_REGION)


# ── Logging setup ─────────────────────────────────────────────────────────────

def setup_logging():
    os.makedirs("logs", exist_ok=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("google_adk").setLevel(logging.WARNING)
    logging.getLogger("google_genai.models").setLevel(logging.WARNING)
    logging.getLogger("runner").setLevel(logging.INFO)

log = logging.getLogger("runner")


# ── Session ID generation ─────────────────────────────────────────────────────

def new_session_id() -> str:
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    short_id = uuid.uuid4().hex[:6]
    return f"run_{ts}_{short_id}"


# ── MongoDB session helpers ───────────────────────────────────────────────────

def list_sessions_from_mongo(limit: int = 10) -> list[dict]:
    """Return the most recent sessions from MongoDB, newest first."""
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=4000)
        db     = client[MONGO_DB_NAME]
        docs   = list(
            db.sessions.find(
                {},
                {"_id": 1, "vessel_id": 1, "timestamp": 1}
            ).sort("timestamp", -1).limit(limit)
        )
        client.close()
        return docs
    except Exception as exc:
        log.error("Could not list sessions: %s", exc)
        return []


def load_session_history(session_id: str) -> list:
    """Load adk_chat_history from MongoDB and deserialize to Event objects."""
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=4000)
        db     = client[MONGO_DB_NAME]
        doc    = db.sessions.find_one({"_id": session_id})
        client.close()
    except Exception as exc:
        log.error("Could not load session: %s", exc)
        return []

    if not doc or "adk_chat_history" not in doc:
        return []

    return sanitize_history(_deserialize_history(doc["adk_chat_history"]))


def save_session_to_mongo(session_id: str, vessel_id: str, session_obj,
                          main_session_id: str | None = None,
                          subsession_id: str | None = None):
    """Serialize the current session events and upsert to MongoDB."""
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=4000)
        db     = client[MONGO_DB_NAME]
        fields = {
            "vessel_id":        vessel_id,
            "timestamp":        datetime.now(timezone.utc).isoformat(),
            "adk_chat_history": _serialize_history(session_obj.events)
        }
        if main_session_id:
            fields["main_session_id"] = main_session_id
        if subsession_id:
            fields["subsession_id"] = subsession_id
        db.sessions.update_one(
            {"_id": session_id},
            {"$set": fields},
            upsert=True
        )
        client.close()
        log.info("Session '%s' saved to MongoDB.", session_id)
    except Exception as exc:
        log.error("Could not save session: %s", exc)


# ── ADK Event serialization ───────────────────────────────────────────────────

def _serialize_history(events_list) -> list:
    serialized = []
    for event in events_list:
        if not hasattr(event, "content") or not event.content:
            continue
        parts_list = []
        for part in event.content.parts:
            if hasattr(part, "text") and part.text:
                parts_list.append({"text": part.text})
            elif hasattr(part, "function_call") and part.function_call:
                parts_list.append({"function_call": {
                    "name": part.function_call.name,
                    "args": dict(part.function_call.args)
                }})
            elif hasattr(part, "function_response") and part.function_response:
                parts_list.append({"function_response": {
                    "name":     part.function_response.name,
                    "response": part.function_response.response
                }})
        serialized.append({
            "role":   event.content.role,
            "author": getattr(event, "author", "system"),
            "parts":  parts_list
        })
    return serialized


def _deserialize_history(history_dicts: list) -> list:
    deserialized = []
    for doc in history_dicts:
        parts = []
        for p in doc.get("parts", []):
            if "text" in p:
                parts.append(genai_types.Part(text=p["text"]))
            elif "function_call" in p:
                parts.append(genai_types.Part(
                    function_call=genai_types.FunctionCall(
                        name=p["function_call"]["name"],
                        args=p["function_call"]["args"]
                    )
                ))
            elif "function_response" in p:
                parts.append(genai_types.Part(
                    function_response=genai_types.FunctionResponse(
                        name=p["function_response"]["name"],
                        response=p["function_response"]["response"]
                    )
                ))
        content = genai_types.Content(role=doc.get("role", "user"), parts=parts)
        deserialized.append(Event(author=doc.get("author", "system"), content=content))
    return deserialized


def sanitize_history(events: list) -> list:
    """
    Drop any trailing turns that would cause Gemini's
    'function_call count != function_response count' error.

    Gemini requires every function_call in a model turn to be matched by
    an equal number of function_response parts in the immediately following
    user turn.  Interrupted sessions can violate this.  We walk the list
    and truncate at the first broken pair so the session resumes cleanly.
    """
    if not events:
        return events

    safe: list = []
    i = 0
    while i < len(events):
        event   = events[i]
        content = getattr(event, "content", None)
        if not content:
            safe.append(event)
            i += 1
            continue

        parts    = getattr(content, "parts", []) or []
        role     = getattr(content, "role", "")
        fc_count = sum(1 for p in parts if getattr(p, "function_call", None))
        fr_count = sum(1 for p in parts if getattr(p, "function_response", None))

        if role == "model" and fc_count > 0:
            if i + 1 < len(events):
                next_parts  = getattr(getattr(events[i + 1], "content", None), "parts", []) or []
                next_fr_cnt = sum(1 for p in next_parts if getattr(p, "function_response", None))
                if next_fr_cnt == fc_count:
                    safe.append(event)
                    safe.append(events[i + 1])
                    i += 2
                    continue
            # Unmatched tool call — truncate here
            break
        elif role == "user" and fr_count > 0 and fc_count == 0:
            # Orphaned response turn (model turn was already dropped) — skip
            i += 1
            continue
        else:
            safe.append(event)
            i += 1

    return safe

def append_to_log(session_id: str, event_type: str, data):
    log_file = f"logs/{session_id}.jsonl"
    record   = {
        "timestamp":  datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "event_type": event_type,
        "data":       data
    }
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


# ── Event serializer for display ──────────────────────────────────────────────

def _serialize_event(event) -> dict:
    out = {"type": "unknown"}

    if hasattr(event, "is_final_response") and event.is_final_response():
        out["type"] = "final_response"
        if hasattr(event, "response") and event.response:
            out["text"] = getattr(event.response, "text", str(event.response))
        return out

    content = getattr(event, "content", None)
    if not content:
        return out

    for part in getattr(content, "parts", []) or []:
        fn = getattr(part, "function_call", None)
        if fn:
            out["type"]      = "tool_call"
            out["tool_name"] = getattr(fn, "name", "unknown")
            out["args"]      = dict(getattr(fn, "args", {}) or {})
            return out

        fr = getattr(part, "function_response", None)
        if fr:
            out["type"]      = "tool_result"
            out["tool_name"] = getattr(fr, "name", "unknown")
            raw              = getattr(fr, "response", {})
            out["result"]    = raw if isinstance(raw, dict) else {"raw": str(raw)}
            return out

        text = getattr(part, "text", None)
        if text:
            out["type"] = "model_text"
            out["text"] = text
            return out

    return out


# ── Core turn execution ───────────────────────────────────────────────────────

async def run_turn(
    runner: Runner,
    session_service: InMemorySessionService,
    session_id: str,
    message_text: str,
    vessel_id: str = "unknown",
    *,
    event_callback=None
) -> list:
    """
    Send one message to the agent and stream all events.
    Saves session state to MongoDB after the turn completes.
    Returns the list of serialized event dicts.
    """
    agent_events = []

    async for event in runner.run_async(
        user_id="system",
        session_id=session_id,
        new_message=genai_types.Content(
            role="user",
            parts=[genai_types.Part(text=message_text)]
        )
    ):
        event_dict = _serialize_event(event)
        agent_events.append(event_dict)
        etype = event_dict.get("type")

        if event_callback:
            await event_callback(event_dict)

        # Terminal output
        if etype == "tool_call":
            tool  = event_dict.get("tool_name", "?")
            args  = json.dumps(event_dict.get("args", {}))[:160]
            print(f"\n  → [{tool}] {args}")
            append_to_log(session_id, "tool_call", {"tool": tool, "args": event_dict.get("args")})

        elif etype == "tool_result":
            tool   = event_dict.get("tool_name", "?")
            result = str(event_dict.get("result", ""))[:200]
            print(f"  ← [{tool}] {result}")
            append_to_log(session_id, "tool_result", {"tool": tool, "result": event_dict.get("result")})

        elif etype == "model_text":
            text = event_dict.get("text", "").strip()
            if text:
                print(f"\n  💭 {text[:300]}")
            append_to_log(session_id, "agent_thought", event_dict.get("text"))

        elif etype == "final_response":
            text = event_dict.get("text", "").strip()
            if text:
                print(f"\n{'─'*60}")
                print(f"  Agent: {text}")
                print(f"{'─'*60}")
            append_to_log(session_id, "final_response", text)

    # Save full session to MongoDB after every turn
    session_obj = await session_service.get_session(
        app_name="autonomous-routing", user_id="system", session_id=session_id
    )
    if session_obj:
        save_session_to_mongo(session_id, vessel_id, session_obj)

    return agent_events


# ── Trigger message builder ───────────────────────────────────────────────────

def build_trigger_message(
    main_session_id: str,
    subsession_id: str,
    vessel_id: str,
    closed_port: str,
    disruption_reason: str,
    limit: int
) -> str:
    return (
        f"DISRUPTION ALERT\n"
        f"{'='*40}\n"
        f"Main Session ID: {main_session_id}\n"
        f"Subsession ID:   {subsession_id}\n"
        f"Vessel ID:       {vessel_id}\n"
        f"Port Closed:     {closed_port}\n"
        f"Reason:          {disruption_reason}\n"
        f"N (evaluate this many candidate ports): {limit}\n\n"
        f"Begin autonomous rerouting optimization. Evaluate all {limit} "
        f"candidate ports, calculate costs for every approved one, and "
        f"select the mathematically cheapest compliant route."
    )


# ── Interactive CLI ───────────────────────────────────────────────────────────

def print_banner():
    print("\n" + "═"*62)
    print("  🚢  AUTONOMOUS REROUTING ENGINE  v3  — Optimizer Mode")
    print("═"*62)

def print_menu():
    print("\n  [1]  New rerouting session        (single vessel)")
    print("  [2]  Resume previous session")
    print("  [3]  Exit")
    print("  [4]  Port Disruption Mode          (describe incident in plain English)")
    print()

def prompt_new_session_inputs() -> tuple[str, str, str, int]:
    """Ask the user for disruption details. Returns (vessel_id, closed_port, reason, limit)."""
    print("\n  ── New Session Setup ──────────────────────────────────")
    vessel_id = input("  Vessel ID          [default: MV_ATLAS_001]: ").strip() or "MV_ATLAS_001"
    closed_port = input("  Closed Port        [default: Port of Long Beach]: ").strip() or "Port of Long Beach"
    reason    = input("  Disruption Reason  [default: Labor Strike]: ").strip() or "Labor Strike"
    limit_str = input("  Ports to evaluate  [default: 3]: ").strip()
    limit     = int(limit_str) if limit_str.isdigit() else 3
    return vessel_id, closed_port, reason, limit

def pick_session_from_list() -> str | None:
    """Show recent sessions and let the user pick one. Returns session_id or None."""
    sessions = list_sessions_from_mongo(limit=10)
    if not sessions:
        print("\n  No previous sessions found in MongoDB.")
        return None

    print("\n  ── Recent Sessions ─────────────────────────────────────")
    for i, s in enumerate(sessions, 1):
        ts     = s.get("timestamp", "unknown time")[:19].replace("T", " ")
        vessel = s.get("vessel_id", "?")
        sid    = s["_id"]
        print(f"  [{i}]  {sid}  |  {vessel}  |  {ts}")

    print()
    choice = input("  Select [1-N] or type a full session ID: ").strip()

    if choice.isdigit():
        idx = int(choice) - 1
        if 0 <= idx < len(sessions):
            return sessions[idx]["_id"]
        print("  Invalid selection.")
        return None
    elif choice:
        return choice
    return None



# ── Port Disruption Mode helpers ─────────────────────────────────────────────

def get_vessels_for_port(closed_port: str) -> list[str]:
    """Query MongoDB for all vessels whose original_port matches closed_port."""
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=4000)
        db     = client[MONGO_DB_NAME]
        docs   = list(db.vessels.find({"original_port": closed_port}, {"_id": 1}))
        client.close()
        return [d["_id"] for d in docs]
    except Exception as exc:
        log.error("Could not query vessels for port '%s': %s", closed_port, exc)
        return []


def create_main_session(closed_port: str, disruption_reason: str, limit: int) -> str:
    """Insert a main_session document and return its _id."""
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    short_id = uuid.uuid4().hex[:6]
    main_sid = f"main_{ts}_{short_id}"
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=4000)
        db     = client[MONGO_DB_NAME]
        db.main_sessions.insert_one({
            "_id":               main_sid,
            "closed_port":       closed_port,
            "disruption_reason": disruption_reason,
            "limit":             limit,
            "subsession_ids":    [],
            "agent_memory":      None,
            "timestamp":         datetime.now(timezone.utc).isoformat()
        })
        client.close()
        log.info("Main session '%s' created.", main_sid)
    except Exception as exc:
        log.error("Could not create main session: %s", exc)
    return main_sid


def register_subsession(main_session_id: str, subsession_id: str):
    """Push a subsession_id into the main_session's subsession_ids array."""
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=4000)
        db     = client[MONGO_DB_NAME]
        db.main_sessions.update_one(
            {"_id": main_session_id},
            {"$push": {"subsession_ids": subsession_id}}
        )
        client.close()
    except Exception as exc:
        log.error("Could not register subsession '%s': %s", subsession_id, exc)

def save_trigger_memory(main_session_id: str, trigger_session_obj) -> None:
    """Serialize the trigger agent's ADK session and write it to main_sessions.agent_memory."""
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=4000)
        db     = client[MONGO_DB_NAME]
        db.main_sessions.update_one(
            {"_id": main_session_id},
            {"$set": {"agent_memory": _serialize_history(trigger_session_obj.events)}}
        )
        client.close()
        log.info("Trigger memory saved → main_session '%s'.", main_session_id)
    except Exception as exc:
        log.error("Could not save trigger memory: %s", exc)

async def run_single_vessel_subsession(
    vessel_id: str,
    closed_port: str,
    disruption_reason: str,
    limit: int,
    main_session_id: str,
    runner: Runner,
    session_service: InMemorySessionService
) -> dict:
    """
    Run one full agent session for a single vessel.
    Identical to option [1] but stamps main_session_id and subsession_id onto
    the MongoDB session doc. Returns a summary dict for the results table.
    """
    session_id = new_session_id()
    register_subsession(main_session_id, session_id)

    await session_service.create_session(
        app_name="autonomous-routing",
        user_id="system",
        session_id=session_id
    )

    print(f"\n  ┌─ [{vessel_id}] subsession {session_id}")

    # Pass BOTH main_session_id and session_id (which acts as the subsession_id)
    trigger = build_trigger_message(main_session_id, session_id, vessel_id, closed_port, disruption_reason, limit)
    append_to_log(session_id, "trigger", trigger)

    agent_events = []
    async for event in runner.run_async(
        user_id="system",
        session_id=session_id,
        new_message=genai_types.Content(
            role="user",
            parts=[genai_types.Part(text=trigger)]
        )
    ):
        event_dict = _serialize_event(event)
        agent_events.append(event_dict)
        etype = event_dict.get("type")

        if etype == "tool_call":
            tool = event_dict.get("tool_name", "?")
            print(f"  │  → [{vessel_id}] [{tool}]")
            append_to_log(session_id, "tool_call", {"tool": tool, "args": event_dict.get("args")})

        elif etype == "tool_result":
            tool = event_dict.get("tool_name", "?")
            print(f"  │  ← [{vessel_id}] [{tool}] done")
            append_to_log(session_id, "tool_result", {"tool": tool, "result": event_dict.get("result")})

        elif etype == "final_response":
            text = event_dict.get("text", "").strip()
            if text:
                print(f"  │  ✓ [{vessel_id}] {text[:120]}")
            append_to_log(session_id, "final_response", text)

    # Save with parent session linkage
    session_obj = await session_service.get_session(
        app_name="autonomous-routing", user_id="system", session_id=session_id
    )
    if session_obj:
        save_session_to_mongo(
            session_id, vessel_id, session_obj,
            main_session_id=main_session_id,
            subsession_id=session_id
        )

    print(f"  └─ [{vessel_id}] complete → {session_id}")
    return {"vessel_id": vessel_id, "session_id": session_id, "events": len(agent_events)}


async def run_port_disruption_mode(
    closed_port: str,
    disruption_reason: str,
    limit: int,
    runner: Runner,
    session_service: InMemorySessionService,
    main_session_id: str | None = None
):
    """
    Orchestrator: find all vessels going to closed_port, create a main session,
    then run N parallel agent subsessions — one per vessel.
    If main_session_id is provided (from the trigger agent), use it directly.
    """
    vessel_ids = get_vessels_for_port(closed_port)
    if not vessel_ids:
        print(f"\n  No vessels found with original_port = '{closed_port}'.")
        print("  Check the port name matches exactly what's stored in MongoDB.\n")
        return

    if main_session_id is None:
        main_session_id = create_main_session(closed_port, disruption_reason, limit)

    print(f"\n  ── Port Disruption Mode ─────────────────────────────────")
    print(f"  Main session : {main_session_id}")
    print(f"  Port closed  : {closed_port}  ({disruption_reason})")
    print(f"  Vessels found: {len(vessel_ids)}")
    for v in vessel_ids:
        print(f"    • {v}")
    print(f"\n  Launching {len(vessel_ids)} parallel subsessions...\n")

    tasks = [
        run_single_vessel_subsession(
            vessel_id, closed_port, disruption_reason, limit,
            main_session_id, runner, session_service
        )
        for vessel_id in vessel_ids
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Summary table
    print(f"\n{'='*62}")
    print(f"  PORT DISRUPTION RESULTS — {main_session_id}")
    print(f"{'='*62}")
    for r in results:
        if isinstance(r, Exception):
            print(f"  ✗  ERROR: {r}")
        else:
            print(f"  ✓  {str(r['vessel_id']):20s}  →  session {r['session_id']}")
    print(f"{'='*62}\n")
    print(f"  All subsessions saved under main_session_id: {main_session_id}")
    print(f"  Query: db.sessions.find({{main_session_id: '{main_session_id}'}})\n")


def prompt_port_disruption_inputs() -> tuple[str, str, int]:
    """Ask for port name, disruption reason, and limit."""
    print("\n  ── Port Disruption Mode Setup ─────────────────────────")
    closed_port = input("  Closed Port        [default: Port of Long Beach]: ").strip() or "Port of Long Beach"
    reason      = input("  Disruption Reason  [default: Labor Strike]: ").strip() or "Labor Strike"
    limit_str   = input("  Ports to evaluate  [default: 3]: ").strip()
    limit       = int(limit_str) if limit_str.isdigit() else 3
    return closed_port, reason, limit

# ── Trigger Agent CLI loop ────────────────────────────────────────────────────


async def run_trigger_agent_cli(
    runner: Runner,
    session_service: InMemorySessionService
) -> None:
    """
    Natural-language interface for port disruption dispatch.
    Spins up the TriggerAgent, creates a main_session in MongoDB,
    then enters a chat loop. After every turn, the trigger agent's
    full conversation is persisted to main_sessions.agent_memory.
    """
    from agents.trigger_agent import build_trigger_agent
    from session_service import InMemorySessionService as _ISS

    # ── Create the main_session document first ────────────────────────────────
    # We need the ID before the agent runs so the dispatch tool can reference it.
    # Port/cause/limit are unknown at this point — the agent will extract them.
    # We insert a placeholder and update after the first dispatch.
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    short_id = uuid.uuid4().hex[:6]
    main_session_id = f"main_{ts}_{short_id}"
    try:
        _client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=4000)
        _db     = _client[MONGO_DB_NAME]
        _db.main_sessions.insert_one({
            "_id":               main_session_id,
            "closed_port":       None,
            "disruption_reason": None,
            "limit":             None,
            "subsession_ids":    [],
            "agent_memory":      None,
            "timestamp":         datetime.now(timezone.utc).isoformat()
        })
        _client.close()
        log.info("Main session '%s' pre-created for trigger agent.", main_session_id)
    except Exception as exc:
        log.error("Could not pre-create main session: %s", exc)

    # ── Build dispatch tool as a closure over main_session_id ─────────────────
    async def dispatch_fleet_rerouting(
        closed_port: str,
        cause: str,
        limit: int = 3
    ) -> dict:
        """
        Trigger parallel rerouting for all vessels going to closed_port.

        Args:
            closed_port: full name of the disrupted port (e.g. "Port of Long Beach")
            cause:       reason for disruption (e.g. "Labor Strike")
            limit:       number of alternative ports to evaluate per vessel (default 3)

        Returns:
            dict with vessels_found, main_session_id, status
        """
        # Update the main_session with now-known fields
        try:
            _c  = MongoClient(MONGO_URI, serverSelectionTimeoutMS=4000)
            _db = _c[MONGO_DB_NAME]
            _db.main_sessions.update_one(
                {"_id": main_session_id},
                {"$set": {
                    "closed_port":       closed_port,
                    "disruption_reason": cause,
                    "limit":             limit
                }}
            )
            _c.close()
        except Exception as exc:
            log.error("Could not update main session fields: %s", exc)

        await run_port_disruption_mode(
            closed_port, cause, limit, runner, session_service,
            main_session_id=main_session_id
        )

        vessel_ids = get_vessels_for_port(closed_port)
        return {
            "vessels_found":  len(vessel_ids),
            "main_session_id": main_session_id,
            "closed_port":    closed_port,
            "cause":          cause,
            "limit":          limit,
            "status":         "dispatched"
        }

    # ── Build compliance query tool as a closure ──────────────────────────────
    def get_subsession_compliance(vessel_id: str) -> dict:
        """
        Retrieve the compliance_checks for the sub-session that handled a specific vessel.
        Use this when the operator asks about alternative ports found for a vessel.

        Args:
            vessel_id: the vessel's ID (e.g. "MV_ATLAS_001")

        Returns:
            dict with vessel_id, sub_session_id, and compliance_checks list
        """
        try:
            client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=4000)
            db     = client[MONGO_DB_NAME]
            # Find the sub-session for this vessel under this main_session
            doc = db.sessions.find_one(
                {
                    "main_session_id": main_session_id,
                    "vessel_id":       vessel_id
                },
                {"_id": 1, "compliance_checks": 1}
            )
            client.close()
        except Exception as exc:
            log.error("Could not query compliance for vessel '%s': %s", vessel_id, exc)
            return {"error": str(exc)}

        if not doc:
            return {
                "vessel_id":      vessel_id,
                "sub_session_id": None,
                "compliance_checks": [],
                "message": (
                    f"No sub-session found for vessel '{vessel_id}' "
                    f"under main session '{main_session_id}'. "
                    "Make sure the dispatch has completed and the vessel ID is correct."
                )
            }

        return {
            "vessel_id":         vessel_id,
            "sub_session_id":    doc["_id"],
            "compliance_checks": doc.get("compliance_checks", [])
        }

    # ── Build trigger agent + its own session service ─────────────────────────
    trigger_agent    = build_trigger_agent(dispatch_fleet_rerouting, get_subsession_compliance)
    trigger_sess_id  = f"trigger_{main_session_id}"
    trigger_sess_svc = _ISS()
    trigger_runner   = Runner(
        agent=trigger_agent,
        app_name="autonomous-routing-trigger",
        session_service=trigger_sess_svc
    )

    await trigger_sess_svc.create_session(
        app_name="autonomous-routing-trigger",
        user_id="system",
        session_id=trigger_sess_id
    )

    print("\n  ── Port Disruption Mode — Natural Language Interface ────")
    print(f"  Main session: {main_session_id}")
    print("  Describe the incident. Examples:")
    print("    \"Port of Long Beach is down due to a labor strike\"")
    print("    \"Hurricane warning at Port of Oakland, find 5 alternatives\"")
    print("    \"What alternatives did the agent find for MV_ATLAS_001?\"")
    print("  Type 'menu' to go back.\n")

    # ── Chat loop ─────────────────────────────────────────────────────────────
    while True:
        try:
            user_input = input("  You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Interrupted.")
            break

        if not user_input:
            continue
        if user_input.lower() in ("menu", "back", "exit", "quit", "done", "q"):
            break

        # Run one turn
        async for event in trigger_runner.run_async(
            user_id="system",
            session_id=trigger_sess_id,
            new_message=genai_types.Content(
                role="user",
                parts=[genai_types.Part(text=user_input)]
            )
        ):
            ev    = _serialize_event(event)
            etype = ev.get("type")

            if etype == "tool_call":
                tool = ev.get("tool_name", "?")
                args = json.dumps(ev.get("args", {}))[:200]
                print(f"\n  → [{tool}] {args}")

            elif etype == "final_response":
                text = ev.get("text", "").strip()
                if text:
                    print(f"\n  Coordinator: {text}\n")

            elif etype == "model_text":
                text = ev.get("text", "").strip()
                if text:
                    print(f"  💭 {text[:200]}")

        # ── Persist trigger agent memory after every turn ─────────────────────
        trigger_sess_obj = await trigger_sess_svc.get_session(
            app_name="autonomous-routing-trigger",
            user_id="system",
            session_id=trigger_sess_id
        )
        if trigger_sess_obj:
            save_trigger_memory(main_session_id, trigger_sess_obj)

async def interactive_main():
    setup_logging()
    print_banner()

    # ── Build shared resources once (kept alive for the whole session) ────────
    print("\n  Initializing toolsets...")
    # MongoDB MCP toolset — root agent uses this for Atlas reads via MCP protocol
    try:
        mongo_toolset = make_mongo_toolset(MONGO_URI)
        print("  MongoDB MCP server connected (Atlas read/write via MCP).")
    except Exception as exc:
        mongo_toolset = None
        print(f"  MongoDB MCP unavailable (falling back to pymongo): {exc}")
    root_agent      = build_root_agent(mongo_toolset)
    session_service = InMemorySessionService()
    runner          = Runner(
        agent=root_agent,
        app_name="autonomous-routing",
        session_service=session_service
    )
    print("  Toolsets ready.\n")

    # ── Main menu loop ────────────────────────────────────────────────────────
    while True:
        print_menu()
        choice = input("  Choice: ").strip()

        # ── EXIT ──────────────────────────────────────────────────────────────
        if choice == "3" or choice.lower() in ("exit", "quit", "q"):
            print("\n  Goodbye.\n")
            break

        elif choice == "4":
            await run_trigger_agent_cli(runner, session_service)
            continue

# ── NEW SESSION ───────────────────────────────────────────────────────
        elif choice == "1":
            vessel_id, closed_port, reason, limit = prompt_new_session_inputs()
            session_id = new_session_id()

            await session_service.create_session(
                app_name="autonomous-routing",
                user_id="system",
                session_id=session_id
            )

            print(f"\n  ── Session: {session_id} ────────────────────────────────")
            print(f"  Evaluating {limit} candidate ports for vessel {vessel_id}...\n")

            # Fallback for manual single runs: use session_id for both fields
            trigger = build_trigger_message(session_id, session_id, vessel_id, closed_port, reason, limit)
            append_to_log(session_id, "trigger", trigger)

            await run_turn(runner, session_service, session_id, trigger, vessel_id=vessel_id)

        # ── RESUME SESSION ────────────────────────────────────────────────────
        elif choice == "2":
            session_id = pick_session_from_list()
            if not session_id:
                continue

            # Create in-memory session
            await session_service.create_session(
                app_name="autonomous-routing",
                user_id="system",
                session_id=session_id
            )

            # Inject saved history
            history = load_session_history(session_id)
            if history:
                session_obj = await session_service.get_session(
                    app_name="autonomous-routing", user_id="system", session_id=session_id
                )
                session_obj.events.extend(history)
                print(f"\n  Resumed session '{session_id}' — {len(history)} previous turns loaded.")
            else:
                print(f"\n  Session '{session_id}' found but no history to load.")

            vessel_id = "unknown"  # will be evident from context

            # Drop straight into chat loop for resumed sessions
            print("  You can now ask about any previous decisions.\n")

        else:
            print("  Invalid choice. Enter 1, 2, 3, or 4.")
            continue

        # ── CHAT LOOP (runs after both new and resume) ────────────────────────
        print(f"\n  Type your message below. Enter 'menu' to return to the main menu.")
        print(f"  Examples: 'Why did you reject Oakland?'")
        print(f"            'What if cargo value was $20M?'")
        print(f"            'Show me cost breakdown for each approved port.'")
        print()

        while True:
            try:
                user_input = input("  You: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n  Interrupted. Returning to menu.")
                break

            if not user_input:
                continue

            if user_input.lower() in ("menu", "back", "exit", "quit", "q"):
                break

            await run_turn(
                runner, session_service, session_id,
                user_input, vessel_id=vessel_id
            )


# ── Callable from Streamlit dashboard ────────────────────────────────────────

async def run_rerouting_agent(
    vessel_id: str,
    disruption_reason: str,
    closed_port: str,
    session_id: str,
    limit: int = 3,
    *,
    event_callback=None
) -> dict:
    """
    Programmatic entry point for the Streamlit dashboard.
    Creates a runner, fires the agent, streams events via event_callback.
    """
    # MongoDB MCP toolset — connect for Atlas reads via MCP (falls back gracefully)
    try:
        mongo_toolset = make_mongo_toolset(MONGO_URI)
    except Exception:
        mongo_toolset = None
    root_agent      = build_root_agent(mongo_toolset)
    session_service = InMemorySessionService()

    await session_service.create_session(
        app_name="autonomous-routing", user_id="system", session_id=session_id
    )

    # Load existing history if resuming
    history = load_session_history(session_id)
    if history:
        session_obj = await session_service.get_session(
            app_name="autonomous-routing", user_id="system", session_id=session_id
        )
        session_obj.events.extend(history)

    runner = Runner(agent=root_agent, app_name="autonomous-routing", session_service=session_service)

    # Use session_id for both fields here too if main_session_id isn't explicitly passed
    trigger    = build_trigger_message(session_id, session_id, vessel_id, closed_port, disruption_reason, limit)
    all_events = []

    async for event in runner.run_async(
        user_id="system",
        session_id=session_id,
        new_message=genai_types.Content(role="user", parts=[genai_types.Part(text=trigger)])
    ):
        event_dict = _serialize_event(event)
        all_events.append(event_dict)
        if event_callback:
            await event_callback(event_dict)

    session_obj = await session_service.get_session(
        app_name="autonomous-routing", user_id="system", session_id=session_id
    )
    if session_obj:
        save_session_to_mongo(session_id, vessel_id, session_obj)

    return {"status": "COMPLETE", "events": all_events, "session_id": session_id}


# ── Entrypoint ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    asyncio.run(interactive_main())