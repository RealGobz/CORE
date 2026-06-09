# ui/app.py
import os
import sys
import uuid
import json
import asyncio
from datetime import datetime, timezone

import streamlit as st
from pymongo import MongoClient

# Fix for Windows asyncio loop if applicable
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Add the parent directory to the path so we can import project modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import runner as base_runner
from config import MONGO_URI, MONGO_DB_NAME
from toolsets.connections import make_mongo_toolset
from agents.root_agent import build_root_agent
from agents.trigger_agent import build_trigger_agent
from session_service import InMemorySessionService
from google.adk.runners import Runner
from google.genai import types as genai_types
from runner import (
    get_vessels_for_port,
    run_port_disruption_mode,
    _serialize_event,
    save_trigger_memory,
    _deserialize_history,
    save_session_to_mongo
)

# ─── History Sanitizer ─────────────────────────────────────────────────────────
def sanitize_history(events: list) -> list:
    """
    Remove any trailing turns that would cause Gemini's
    'function_call count != function_response count' error.

    Gemini requires that every function_call part in a model turn is
    answered by an exactly matching function_response part in the very
    next user turn.  When a session is interrupted mid-tool the stored
    history can violate this, crashing the next run_async call.

    Strategy: walk the event list and drop any suffix where:
      • a model turn contains function_call parts with no following
        user turn that contains the same number of function_response parts.
    We also drop any lone function_response user turn that has no
    preceding model function_call turn.
    """
    if not events:
        return events

    safe: list = []
    i = 0
    while i < len(events):
        event = events[i]
        content = getattr(event, "content", None)
        if not content:
            safe.append(event)
            i += 1
            continue

        parts = getattr(content, "parts", []) or []
        role  = getattr(content, "role", "")

        # Count function_calls in this event
        fc_count = sum(1 for p in parts if getattr(p, "function_call", None))
        fr_count = sum(1 for p in parts if getattr(p, "function_response", None))

        if role == "model" and fc_count > 0:
            # This turn made tool calls — the NEXT turn must have exactly
            # fc_count function_response parts.
            if i + 1 < len(events):
                next_event  = events[i + 1]
                next_parts  = getattr(getattr(next_event, "content", None), "parts", []) or []
                next_fr_cnt = sum(1 for p in next_parts if getattr(p, "function_response", None))
                if next_fr_cnt == fc_count:
                    # Pair is intact — keep both
                    safe.append(event)
                    safe.append(next_event)
                    i += 2
                    continue
                else:
                    # Mismatch — drop this turn and everything after
                    break
            else:
                # Tool call at end of history with no response — drop it
                break

        elif role == "user" and fr_count > 0 and fc_count == 0:
            # Pure function-response turn with no preceding model call
            # (can happen if the model turn was already dropped) — skip
            i += 1
            continue

        else:
            safe.append(event)
            i += 1

    return safe

# ─── Streamlit Page Config ────────────────────────────────────────────────────
st.set_page_config(page_title="CORE", page_icon="🚢", layout="wide")

# Custom CSS for overall polish
st.markdown("""
    <style>
    .block-container { padding-top: 2rem; padding-bottom: 2rem; }
    </style>
""", unsafe_allow_html=True)

# ─── Persistent Database Connection ───────────────────────────────────────────
@st.cache_resource
def get_mongo_client():
    return MongoClient(MONGO_URI, serverSelectionTimeoutMS=4000)

# ─── Robust Memory Extractor ──────────────────────────────────────────────────
def extract_last_model_text(sess_obj):
    if not sess_obj or not sess_obj.events: return ""
    last_ev = sess_obj.events[-1]
    role = last_ev.get("role") if isinstance(last_ev, dict) else getattr(last_ev, "role", "")
    if role != "model": return ""
        
    parts = last_ev.get("parts", []) if isinstance(last_ev, dict) else getattr(last_ev, "parts", [])
    out_text = ""
    for p in parts:
        if isinstance(p, dict) and "text" in p: out_text += p["text"] + "\n"
        elif hasattr(p, "text") and p.text: out_text += p.text + "\n"
    return out_text.strip()

# ─── UI State Synchronization Helpers ─────────────────────────────────────────
def refresh_main_chat_history():
    if not st.session_state.get("main_session_id"): return
    try:
        client = get_mongo_client()
        db = client[MONGO_DB_NAME]
        doc = db.main_sessions.find_one({"_id": st.session_state.main_session_id})
        
        reconstructed = []
        if doc and doc.get("agent_memory"):
            for event in doc["agent_memory"]:
                role = event.get("role")
                ui_role = "assistant" if role == "model" else "user"
                for part in event.get("parts", []):
                    if "text" in part:
                        text_content = part["text"].strip()
                        if text_content: reconstructed.append({"role": ui_role, "content": text_content})
        st.session_state.chat_history = reconstructed
    except Exception as e: pass

def refresh_subsession_chat_history():
    if not st.session_state.get("selected_subsession"): return
    try:
        client = get_mongo_client()
        db = client[MONGO_DB_NAME]
        doc = db.sessions.find_one({"_id": st.session_state.selected_subsession})
        
        reconstructed = []
        if doc and doc.get("adk_chat_history"):
            for event in doc["adk_chat_history"]:
                role = event.get("role")
                ui_role = "assistant" if role == "model" else "user"
                text_content = ""
                for part in event.get("parts", []):
                    if "text" in part: text_content += part["text"].strip() + "\n"
                if text_content.strip(): reconstructed.append({"role": ui_role, "content": text_content.strip()})
        st.session_state.subsession_ui_history = reconstructed
    except Exception as e: pass

# ─── Data Fetching Helpers ────────────────────────────────────────────────────
def fetch_subsessions(main_session_id):
    if not main_session_id: return []
    try:
        client = get_mongo_client()
        db = client[MONGO_DB_NAME]
        sessions = list(db.sessions.find({"main_session_id": main_session_id}))
        results = []
        for s in sessions:
            sub_id = s["_id"]
            vessel_id = s.get("vessel_id", "Unknown Vessel")
            log_doc = db.rerouting_logs.find_one({"subsession_id": sub_id})
            
            status = "⏳ Running..."
            vessel_name = vessel_id
            if log_doc:
                vessel_name = log_doc.get("vessel_name", vessel_id)
                res_status = log_doc.get("result", {}).get("status", "")
                if res_status == "RESOLVED": status = "✅ RESOLVED"
                elif res_status == "ESCALATED": status = "⚠️ ESCALATED"
                    
            results.append({"subsession_id": sub_id, "vessel_id": vessel_id, "vessel_name": vessel_name, "status": status})
        return results
    except Exception as e: return []

def fetch_manifest_data(subsession_id):
    try:
        client = get_mongo_client()
        db = client[MONGO_DB_NAME]
        return db.rerouting_logs.find_one({"subsession_id": subsession_id})
    except Exception as e: return None

# ─── Session Management Logic ─────────────────────────────────────────────────

def _text_only_history(raw_history: list) -> list:
    """
    Keep only pure-text turns from a stored adk_chat_history.
    Tool call / response turns are agent-internal scaffolding from the
    original autonomous run. Replaying them into a new chat session
    causes Gemini 400 INVALID_ARGUMENT because the new session has no
    matching tool state. We give the agent context via text turns only
    (the trigger message + final summary) so follow-up questions work.
    """
    text_only = []
    for event in raw_history:
        parts = event.get("parts", [])
        if any("function_call" in p or "function_response" in p for p in parts):
            continue
        if any("text" in p and p.get("text", "").strip() for p in parts):
            text_only.append(event)
    return text_only


def setup_subsession(subsession_id):
    if not subsession_id: return
    refresh_subsession_chat_history()
    try:
        async def init_root_memory():
            await st.session_state.root_session_service.create_session(app_name="autonomous-routing", user_id="system", session_id=subsession_id)
            client = get_mongo_client()
            db = client[MONGO_DB_NAME]
            sub_doc = db.sessions.find_one({"_id": subsession_id})
            if sub_doc and sub_doc.get("adk_chat_history"):
                # Strip tool call/response turns — only inject text context
                clean_raw = _text_only_history(sub_doc["adk_chat_history"])
                history_events = _deserialize_history(clean_raw)
                sess_obj = await st.session_state.root_session_service.get_session(app_name="autonomous-routing", user_id="system", session_id=subsession_id)
                if sess_obj: sess_obj.events.extend(history_events)
        asyncio.run(init_root_memory())
    except Exception as e: st.error(f"Error loading subsession memory: {e}")

def setup_session(session_id=None):
    if session_id is None:
        st.session_state.main_session_id = None
        st.session_state.trigger_sess_id = None
        st.session_state.chat_history = []
        st.session_state.selected_subsession = None
        st.session_state.loaded_subsession_id = None
    else:
        if st.session_state.get("main_session_id") != session_id:
            st.session_state.selected_subsession = None 
            st.session_state.loaded_subsession_id = None
            
        st.session_state.main_session_id = session_id
        st.session_state.trigger_sess_id = f"trigger_{session_id}"
        refresh_main_chat_history()

        async def init_trigger():
            await st.session_state.trigger_sess_svc.create_session(app_name="autonomous-routing-trigger", user_id="system", session_id=st.session_state.trigger_sess_id)
            try:
                client = get_mongo_client()
                db = client[MONGO_DB_NAME]
                doc = db.main_sessions.find_one({"_id": session_id})
                if doc and doc.get("agent_memory"):
                    history = sanitize_history(_deserialize_history(doc["agent_memory"]))
                    sess_obj = await st.session_state.trigger_sess_svc.get_session(app_name="autonomous-routing-trigger", user_id="system", session_id=st.session_state.trigger_sess_id)
                    if sess_obj: sess_obj.events.extend(history)
            except Exception: pass
        asyncio.run(init_trigger())

# ─── Initial App Bootstrapping ────────────────────────────────────────────────
if "initialized" not in st.session_state:
    base_runner.setup_logging()
    mongo_toolset = make_mongo_toolset(MONGO_URI)
    root_agent = build_root_agent()
    st.session_state.root_session_service = InMemorySessionService()
    st.session_state.root_runner = Runner(agent=root_agent, app_name="autonomous-routing", session_service=st.session_state.root_session_service)

    async def dispatch_fleet_rerouting(closed_port: str, cause: str, limit: int = 3) -> dict:
        try:
            client = get_mongo_client()
            db = client[MONGO_DB_NAME]
            db.main_sessions.update_one({"_id": st.session_state.main_session_id}, {"$set": {"closed_port": closed_port, "disruption_reason": cause, "limit": limit}})
        except Exception as exc: pass
        await run_port_disruption_mode(closed_port, cause, limit, st.session_state.root_runner, st.session_state.root_session_service, main_session_id=st.session_state.main_session_id)
        vessel_ids = get_vessels_for_port(closed_port)
        return {"vessels_found": len(vessel_ids), "main_session_id": st.session_state.main_session_id, "closed_port": closed_port, "cause": cause, "limit": limit, "status": "dispatched"}

    def get_subsession_compliance(vessel_id: str) -> dict:
        try:
            client = get_mongo_client()
            db = client[MONGO_DB_NAME]
            doc = db.sessions.find_one({"main_session_id": st.session_state.main_session_id, "vessel_id": vessel_id}, {"_id": 1, "compliance_checks": 1})
        except Exception as exc: return {"error": str(exc)}
        if not doc: return {"vessel_id": vessel_id, "sub_session_id": None, "compliance_checks": [], "message": "No sub-session found."}
        return {"vessel_id": vessel_id, "sub_session_id": doc["_id"], "compliance_checks": doc.get("compliance_checks", [])}

    st.session_state.trigger_agent = build_trigger_agent(dispatch_fleet_rerouting, get_subsession_compliance)
    st.session_state.trigger_sess_svc = InMemorySessionService()
    st.session_state.trigger_runner = Runner(agent=st.session_state.trigger_agent, app_name="autonomous-routing-trigger", session_service=st.session_state.trigger_sess_svc)
    setup_session(None)
    st.session_state.initialized = True

if st.session_state.get("selected_subsession") and st.session_state.get("selected_subsession") != st.session_state.get("loaded_subsession_id"):
    setup_subsession(st.session_state.selected_subsession)
    st.session_state.loaded_subsession_id = st.session_state.selected_subsession


# ─── Sidebar: Session Navigation ──────────────────────────────────────────────
with st.sidebar:
    st.markdown("""
        <div style="text-align: center; margin-bottom: 20px;">
            <h1 style="margin: 0; font-size: 2.5rem;">⚙️ CORE</h1>
            <p style="margin: 0; color: #64748b; font-size: 0.9rem;">Compliance-Optimized Rerouting Engine</p>
        </div>
    """, unsafe_allow_html=True)
    
    if st.button("➕ New Disruption Event", use_container_width=True):
        setup_session(None)
        st.rerun()
        
    st.divider()
    st.subheader("Event History")
    try:
        client = get_mongo_client()
        db = client[MONGO_DB_NAME]
        recent_sessions = list(db.main_sessions.find({}, {"_id": 1, "timestamp": 1, "closed_port": 1}).sort("timestamp", -1).limit(15))
        for s in recent_sessions:
            sid = s["_id"]
            port = s.get("closed_port") or "Unspecified Port"
            ts = s.get("timestamp", "")[:16].replace("T", " ")
            is_active = (sid == st.session_state.main_session_id)
            btn_type = "primary" if is_active else "secondary"
            if st.button(f"{port}\n{ts}", key=f"btn_{sid}", use_container_width=True, type=btn_type):
                if not is_active:
                    setup_session(sid)
                    st.rerun()
    except Exception as e: pass

# ─── Main UI Layout ───────────────────────────────────────────────────────────
st.markdown("""
    <div style="margin-bottom: 1rem;">
        <h2 style="margin: 0;">Command Center</h2>
        <p style="margin: 0; color: #94a3b8;">Manage real-time port disruptions and autonomous vessel rerouting.</p>
    </div>
""", unsafe_allow_html=True)

chat_col, panel_col = st.columns([2, 1], gap="large")

with chat_col:
    # =====================================================================
    # UPPER SECTION: TRIGGER AGENT (Main Session - BLUE THEME)
    # =====================================================================
    with st.container(border=True):
        st.markdown(f"""
        <div style="background-color: #0f172a; padding: 12px 20px; border-radius: 8px; border-left: 5px solid #3b82f6; margin-bottom: 15px;">
            <h3 style="margin:0; color: #e2e8f0; font-size: 1.2rem;">🌐 Level 1: Global Fleet Coordinator</h3>
            <p style="margin:0; font-size: 0.85rem; color: #94a3b8;">Main Session: <code>{st.session_state.main_session_id or 'Pending New Session...'}</code></p>
        </div>
        """, unsafe_allow_html=True)

        trigger_chat_container = st.container(height=350, border=False)
        with trigger_chat_container:
            if not st.session_state.chat_history:
                st.info("Describe the port disruption here to initiate a new session... (e.g., 'Port of Seattle is closed, find 3 alternatives')")
            for msg in st.session_state.chat_history:
                with st.chat_message(msg["role"]):
                    st.markdown(msg["content"])

        if prompt := st.chat_input("Dispatch disruption alert to the Fleet Coordinator...", key="trigger_prompt"):
            if st.session_state.main_session_id is None:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                short_id = uuid.uuid4().hex[:6]
                new_id = f"main_{ts}_{short_id}"
                try:
                    client = get_mongo_client()
                    db = client[MONGO_DB_NAME]
                    db.main_sessions.insert_one({
                        "_id": new_id, "closed_port": None, "disruption_reason": None,
                        "limit": None, "subsession_ids": [], "agent_memory": None,
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    })
                except Exception as exc: pass
                st.session_state.main_session_id = new_id
                st.session_state.trigger_sess_id = f"trigger_{new_id}"
                
                async def init_new_trigger():
                    await st.session_state.trigger_sess_svc.create_session(app_name="autonomous-routing-trigger", user_id="system", session_id=st.session_state.trigger_sess_id)
                asyncio.run(init_new_trigger())

            st.session_state.chat_history.append({"role": "user", "content": prompt})
            with trigger_chat_container:
                with st.chat_message("user"): st.markdown(prompt)
                with st.chat_message("assistant"):
                    status_container = st.container()
                    output_placeholder = st.empty()
                    async def process_turn():
                        final_text = ""
                        with status_container:
                            status = st.status("Fleet Coordinator Analyzing...", expanded=True)
                        async for event in st.session_state.trigger_runner.run_async(
                            user_id="system", session_id=st.session_state.trigger_sess_id,
                            new_message=genai_types.Content(role="user", parts=[genai_types.Part(text=prompt)])
                        ):
                            ev = _serialize_event(event)
                            etype = ev.get("type")
                            if etype == "tool_call": status.write(f"🛠 **Routing Data:** `{ev.get('tool_name', '?')}`")
                            elif etype == "tool_result": status.write(f"✅ **Data received:** `{ev.get('tool_name', '?')}`")
                            elif etype in ["model_text", "final_response"]:
                                text = ev.get("text", "")
                                if text and text.strip() not in final_text:
                                    final_text += text + " "
                                    output_placeholder.markdown(final_text + "▌")
                        status.update(label="Dispatch Complete", state="complete", expanded=False)
                        output_placeholder.empty()

                        trigger_sess_obj = await st.session_state.trigger_sess_svc.get_session(app_name="autonomous-routing-trigger", user_id="system", session_id=st.session_state.trigger_sess_id)
                        if trigger_sess_obj:
                            save_trigger_memory(st.session_state.main_session_id, trigger_sess_obj)
                            return extract_last_model_text(trigger_sess_obj)
                        return final_text
                    asyncio.run(process_turn())
            refresh_main_chat_history()
            st.rerun()

    # =====================================================================
    # LOWER SECTION: ROOT AGENT (Subsession Viewer - PURPLE THEME)
    # =====================================================================
    if st.session_state.get("selected_subsession"):
        with st.container(border=True):
            st.markdown(f"""
            <div style="background-color: #1e1b4b; padding: 12px 20px; border-radius: 8px; border-left: 5px solid #8b5cf6; margin-bottom: 15px;">
                <h3 style="margin:0; color: #e0e7ff; font-size: 1.2rem;">🧠 Level 2: Autonomous Root Agent</h3>
                <p style="margin:0; font-size: 0.85rem; color: #a5b4fc;">Subsession: <code>{st.session_state.selected_subsession}</code></p>
            </div>
            """, unsafe_allow_html=True)
            
            log_data = fetch_manifest_data(st.session_state.selected_subsession)
            if log_data:
                res = log_data.get("result", {})
                with st.expander("📄 View Final Routing Manifest", expanded=False):
                    if res.get("status") == "RESOLVED":
                        st.success(f"**Vessel:** {log_data.get('vessel_name')} | **Status:** RESOLVED | **Approved Route:** {res.get('approved_port')}")
                        m1, m2, m3 = st.columns(3)
                        m1.metric("Distance", f"{res.get('distance_km')} km")
                        m2.metric("Total Cost", f"${res.get('total_cost_usd'):,}")
                        m3.metric("Fuel Cost", f"${res.get('fuel_cost_usd'):,}")
                        st.markdown("**✅ Approved Port Compliance:**")
                        st.info(res.get("compliance_approval", {}).get("reasoning", ""))
                        st.markdown("**🧠 Agent Reasoning Summary:**")
                        st.write(log_data.get("agent_reasoning_summary", ""))
                    elif res.get("status") == "ESCALATED":
                        st.error(f"**Vessel:** {log_data.get('vessel_name')} | **Status:** ESCALATED")
                        st.warning(f"**Escalation Reason:** {res.get('escalation_reason')}")
                        st.markdown("**🧠 Agent Reasoning Summary:**")
                        st.write(log_data.get("agent_reasoning_summary", ""))
            
            st.divider()
            st.caption("Chat directly with the Root Agent managing this specific vessel:")
            
            root_chat_container = st.container(height=350, border=False)
            with root_chat_container:
                for msg in st.session_state.get("subsession_ui_history", []):
                    with st.chat_message(msg["role"]):
                        st.markdown(msg["content"])
            
            if root_prompt := st.chat_input("Question the Root Agent...", key="root_prompt"):
                st.session_state.subsession_ui_history.append({"role": "user", "content": root_prompt})
                with root_chat_container:
                    with st.chat_message("user"): st.markdown(root_prompt)
                    with st.chat_message("assistant"):
                        status_container = st.container()
                        output_placeholder = st.empty()
                        async def process_root_turn():
                            final_text = ""
                            with status_container:
                                status = st.status("Root Agent Processing...", expanded=True)
                            async for event in st.session_state.root_runner.run_async(
                                user_id="system", session_id=st.session_state.selected_subsession,
                                new_message=genai_types.Content(role="user", parts=[genai_types.Part(text=root_prompt)])
                            ):
                                ev = _serialize_event(event)
                                etype = ev.get("type")
                                if etype == "tool_call": status.write(f"🛠 **Accessing Knowledge:** `{ev.get('tool_name', '?')}`")
                                elif etype == "tool_result": status.write(f"✅ **Knowledge retrieved:** `{ev.get('tool_name', '?')}`")
                                elif etype in ["model_text", "final_response"]:
                                    text = ev.get("text", "")
                                    if text and text.strip() not in final_text:
                                        final_text += text + " "
                                        output_placeholder.markdown(final_text + "▌")
                            status.update(label="Response Formulated", state="complete", expanded=False)
                            output_placeholder.empty()

                            root_sess_obj = await st.session_state.root_session_service.get_session(app_name="autonomous-routing", user_id="system", session_id=st.session_state.selected_subsession)
                            if root_sess_obj:
                                subs = fetch_subsessions(st.session_state.main_session_id)
                                v_id = next((s["vessel_id"] for s in subs if s["subsession_id"] == st.session_state.selected_subsession), "unknown")
                                save_session_to_mongo(st.session_state.selected_subsession, v_id, root_sess_obj)
                                return extract_last_model_text(root_sess_obj)
                            return final_text
                        asyncio.run(process_root_turn())
                refresh_subsession_chat_history()
                st.rerun()

# ─── Right Panel: Subsessions (ORANGE THEME) ──────────────────────────────────
with panel_col:
    st.markdown("""
    <div style="background-color: #1c1917; padding: 12px 20px; border-radius: 8px; border-left: 5px solid #f97316; margin-bottom: 15px;">
        <h3 style="margin:0; color: #ffedd5; font-size: 1.2rem;">⚙️ Active Reroutes</h3>
        <p style="margin:0; font-size: 0.85rem; color: #fdba74;">Parallel vessel optimizations</p>
    </div>
    """, unsafe_allow_html=True)
    
    if st.button("🔄 Refresh Telemetry", use_container_width=True):
        st.rerun()
        
    subsessions = fetch_subsessions(st.session_state.main_session_id)
    if not subsessions:
        st.info("No vessels currently routing.")
    else:
        for sub in subsessions:
            with st.container(border=True):
                st.markdown(f"**🚢 {sub['vessel_name']}**")
                if "RESOLVED" in sub['status']:
                    st.success(sub['status'])
                elif "ESCALATED" in sub['status']:
                    st.error(sub['status'])
                else:
                    st.warning(sub['status'])
                if st.button("Inspect & Chat", key=f"view_{sub['subsession_id']}", use_container_width=True):
                    st.session_state.selected_subsession = sub['subsession_id']
                    st.rerun()