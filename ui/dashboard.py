"""
ui/dashboard.py
----------------
Streamlit dashboard for the Autonomous Rerouting Engine.

Three-column layout:
  LEFT   — Live disruption alert + trigger button
  CENTER — Real-time agent reasoning log (tool calls + decisions)
  RIGHT  — Final rerouting manifest + human approval buttons

Run locally:
    streamlit run ui/dashboard.py

On Cloud Run:
    The container entrypoint runs this file.
    See deploy/Dockerfile.ui
"""

import sys
import os
import asyncio
import json
import queue
import threading
import time
from pathlib import Path

import streamlit as st
from pymongo import MongoClient

# Project root on path
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import MONGO_URI, MONGO_DB_NAME

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Autonomous Rerouting Engine",
    page_icon="🚢",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ── Styling ───────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .main-header {
        font-size: 1.8rem;
        font-weight: 700;
        color: #1a1a2e;
        margin-bottom: 0.2rem;
    }
    .sub-header {
        font-size: 0.85rem;
        color: #666;
        margin-bottom: 1.5rem;
    }
    .metric-card {
        background: #f8f9fa;
        border-radius: 8px;
        padding: 12px 16px;
        border-left: 4px solid #0066cc;
        margin-bottom: 8px;
    }
    .approved-card {
        border-left-color: #00aa44;
    }
    .rejected-card {
        border-left-color: #cc3300;
    }
    .tool-call-box {
        background: #1e1e2e;
        color: #cdd6f4;
        font-family: monospace;
        font-size: 0.78rem;
        padding: 8px 12px;
        border-radius: 6px;
        margin-bottom: 6px;
        border-left: 3px solid #89b4fa;
    }
    .tool-result-box {
        background: #1e2e1e;
        color: #a6e3a1;
        font-family: monospace;
        font-size: 0.78rem;
        padding: 8px 12px;
        border-radius: 6px;
        margin-bottom: 6px;
        border-left: 3px solid #a6e3a1;
    }
    .citation-box {
        background: #fff8e1;
        border: 1px solid #f0c040;
        border-radius: 6px;
        padding: 10px 14px;
        font-size: 0.85rem;
    }
</style>
""", unsafe_allow_html=True)

# ── MongoDB helper ────────────────────────────────────────────────────────────

@st.cache_resource
def get_mongo_db():
    client = MongoClient(MONGO_URI)
    return client[MONGO_DB_NAME]


def get_active_disruption():
    db = get_mongo_db()
    return db.disruptions.find_one({"active": True})


def get_vessel(vessel_id: str):
    db = get_mongo_db()
    return db.vessels.find_one({"_id": vessel_id})


def get_latest_log(vessel_id: str):
    db = get_mongo_db()
    return db.rerouting_logs.find_one(
        {"vessel_id": vessel_id},
        sort=[("timestamp", -1)]
    )


def update_human_decision(log_id, decision: str):
    db = get_mongo_db()
    db.rerouting_logs.update_one(
        {"_id": log_id},
        {"$set": {"human_decision": decision}}
    )


# ── Run agent in background thread ───────────────────────────────────────────

def _run_agent_thread(vessel_id, disruption_reason, closed_port, event_queue):
    """
    Runs the async agent in a dedicated thread so Streamlit's main thread
    is not blocked. Events are pushed into a queue for the UI to poll.
    """
    import importlib
    # Lazy import to avoid circular issues and give time for Vertex AI init
    runner_module = importlib.import_module("runner")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def callback(event_dict):
        event_queue.put(event_dict)

    try:
        loop.run_until_complete(
            runner_module.run_rerouting_agent(
                vessel_id=vessel_id,
                disruption_reason=disruption_reason,
                closed_port=closed_port,
                event_callback=callback
            )
        )
    except Exception as e:
        event_queue.put({"type": "error", "message": str(e)})
    finally:
        event_queue.put({"type": "done"})
        loop.close()


# ── Format event for display ──────────────────────────────────────────────────

def render_event(event: dict, container):
    """Render a single agent event into the center log column."""
    etype = event.get("type", "unknown")

    if etype == "tool_call":
        tool = event.get("tool_name", "?")
        args = json.dumps(event.get("args", {}), indent=2)
        container.markdown(
            f'<div class="tool-call-box">→ <b>{tool}</b><br><pre>{args}</pre></div>',
            unsafe_allow_html=True
        )

    elif etype == "tool_result":
        tool   = event.get("tool_name", "?")
        result = json.dumps(event.get("result", {}), indent=2)
        # Truncate very long results for readability
        if len(result) > 600:
            result = result[:600] + "\n  ... (truncated)"
        container.markdown(
            f'<div class="tool-result-box">← <b>{tool}</b><br><pre>{result}</pre></div>',
            unsafe_allow_html=True
        )

    elif etype == "model_text":
        text = event.get("text", "")
        if text.strip():
            container.info(f"💭 {text[:400]}")

    elif etype == "error":
        container.error(f"❌ Agent error: {event.get('message')}")


# ── Main UI ───────────────────────────────────────────────────────────────────

def main():
    # Header
    st.markdown('<div class="main-header">🚢 Autonomous Compliance-Constrained Rerouting Engine</div>',
                unsafe_allow_html=True)
    st.markdown('<div class="sub-header">Powered by Gemini 2.5 Pro · Google ADK · MongoDB Atlas</div>',
                unsafe_allow_html=True)

    # Session state initialization
    if "agent_running"  not in st.session_state: st.session_state.agent_running  = False
    if "agent_done"     not in st.session_state: st.session_state.agent_done     = False
    if "event_log"      not in st.session_state: st.session_state.event_log      = []
    if "event_queue"    not in st.session_state: st.session_state.event_queue    = None
    if "agent_thread"   not in st.session_state: st.session_state.agent_thread   = None
    if "start_time"     not in st.session_state: st.session_state.start_time     = None

    # ── Three columns ─────────────────────────────────────────────────────────
    col_left, col_center, col_right = st.columns([1, 1.4, 1.4])

    # ── LEFT: Disruption Alert ────────────────────────────────────────────────
    with col_left:
        st.subheader("🔴 Disruption Alert")

        disruption = get_active_disruption()
        if not disruption:
            st.warning("No active disruptions found.\nRun `python data/seed_mongodb.py` first.")
            return

        vessel = get_vessel(disruption["affected_vessels"][0])
        if not vessel:
            st.error("Vessel document not found in MongoDB.")
            return

        st.error(f"**Port Closed:** {disruption['closed_port']}")
        st.warning(f"**Reason:** {disruption['reason']}")

        st.divider()
        st.markdown("**Affected Vessel**")
        st.markdown(f"- Name: **{vessel['vessel_name']}**")
        st.markdown(f"- Cargo: **{vessel['cargo']['type']}**")
        st.markdown(f"- Temp Sensitive: **{'Yes ⚠️' if vessel['cargo']['temperature_sensitive'] else 'No'}**")
        st.markdown(f"- Containers: **{vessel['cargo']['containers']:,}**")
        st.markdown(f"- Cargo Value: **${vessel['cargo']['value_usd']:,.0f}**")

        st.divider()

        if not st.session_state.agent_running and not st.session_state.agent_done:
            if st.button("🚀 RUN AUTONOMOUS REROUTING", type="primary", use_container_width=True):
                # Start background thread
                q = queue.Queue()
                thread = threading.Thread(
                    target=_run_agent_thread,
                    args=(
                        disruption["affected_vessels"][0],
                        disruption["reason"],
                        disruption["closed_port"],
                        q
                    ),
                    daemon=True
                )
                thread.start()
                st.session_state.agent_running = True
                st.session_state.agent_thread  = thread
                st.session_state.event_queue   = q
                st.session_state.event_log     = []
                st.session_state.start_time    = time.time()
                st.rerun()

        elif st.session_state.agent_running:
            elapsed = int(time.time() - st.session_state.start_time)
            st.info(f"⏱ Agent running... {elapsed}s elapsed")

        elif st.session_state.agent_done:
            elapsed = int(time.time() - st.session_state.start_time)
            st.success(f"✅ Resolved in {elapsed}s")
            if st.button("🔄 Run Again", use_container_width=True):
                st.session_state.agent_running = False
                st.session_state.agent_done    = False
                st.session_state.event_log     = []
                st.rerun()

    # ── CENTER: Live Agent Log ────────────────────────────────────────────────
    with col_center:
        st.subheader("🤖 Agent Reasoning Log")

        log_container = st.container()

        # Drain the queue if agent is running
        if st.session_state.agent_running and st.session_state.event_queue:
            q = st.session_state.event_queue
            new_events = []
            try:
                while True:
                    event = q.get_nowait()
                    new_events.append(event)
                    if event.get("type") == "done":
                        st.session_state.agent_running = False
                        st.session_state.agent_done    = True
                        break
            except queue.Empty:
                pass

            st.session_state.event_log.extend(new_events)

        # Render all events so far
        with log_container:
            if not st.session_state.event_log:
                st.caption("Agent events will appear here once you start the rerouting process.")
            else:
                for event in st.session_state.event_log:
                    if event.get("type") != "done":
                        render_event(event, st)

        # Auto-refresh while running
        if st.session_state.agent_running:
            time.sleep(1.5)
            st.rerun()

    # ── RIGHT: Final Manifest ─────────────────────────────────────────────────
    with col_right:
        st.subheader("📋 Rerouting Manifest")

        if not st.session_state.agent_done:
            st.caption("The approved rerouting manifest will appear here once the agent completes.")
            return

        # Fetch from MongoDB (agent wrote it there)
        vessel_id = disruption["affected_vessels"][0]
        log_doc = get_latest_log(vessel_id)

        if not log_doc:
            st.warning("No manifest found in MongoDB yet. The agent may still be writing it.")
            return

        result = log_doc.get("result", {})
        status = result.get("status", "UNKNOWN")

        if status == "RESOLVED":
            st.success(f"✅ **Approved Route: {result['approved_port']}**")

            # Cost breakdown
            col_a, col_b = st.columns(2)
            with col_a:
                st.metric("Fuel Cost",   f"${result['fuel_cost_usd']:,.0f}")
                st.metric("Tariff Cost", f"${result['tariff_cost_usd']:,.0f}")
            with col_b:
                st.metric("Total Cost",  f"${result['total_cost_usd']:,.0f}")
                st.metric("Distance",    f"{result.get('distance_km', '?'):,.0f} km")

            st.divider()

            # Legal clearance
            lc = result.get("legal_clearance", {})
            st.markdown("**Legal Clearance**")
            st.markdown(
                f'<div class="citation-box">'
                f'📄 <b>{lc.get("cited_section", "?")} — Page {lc.get("cited_page", "?")}</b><br>'
                f'{lc.get("compliance_reasoning", "")}'
                f'</div>',
                unsafe_allow_html=True
            )

            # Rejected routes
            rejected = log_doc.get("rejected_ports", [])
            if rejected:
                st.divider()
                st.markdown("**Rejected Routes**")
                for r in rejected:
                    with st.expander(f"❌ {r.get('port', '?')} — {r.get('rejection_reason', '')[:60]}"):
                        st.markdown(f"**Reason:** {r.get('rejection_reason')}")
                        st.caption(f"Cited: {r.get('cited_section', 'N/A')}")

            st.divider()

            # Human approval buttons
            human_decision = log_doc.get("human_decision", "PENDING")
            if human_decision == "PENDING":
                st.markdown("**Human Decision Required**")
                btn_col1, btn_col2 = st.columns(2)
                with btn_col1:
                    if st.button("✅ APPROVE", type="primary", use_container_width=True):
                        update_human_decision(log_doc["_id"], "APPROVED")
                        st.balloons()
                        st.success("Route approved and dispatched to vessel.")
                with btn_col2:
                    if st.button("❌ REJECT", use_container_width=True):
                        update_human_decision(log_doc["_id"], "REJECTED")
                        st.warning("Route rejected. Escalating to senior manager.")
            elif human_decision == "APPROVED":
                st.success("✅ Human approved — vessel dispatched.")
            elif human_decision == "REJECTED":
                st.error("❌ Human rejected — escalated to senior manager.")

        elif status == "ESCALATED":
            st.error("⚠️ No compliant route found — Escalated to Human Manager")
            st.markdown(f"**Reason:** {result.get('escalation_reason', 'N/A')}")

            ports_tried = result.get("ports_tried", [])
            if ports_tried:
                st.markdown("**All ports attempted:**")
                for p in ports_tried:
                    st.markdown(f"- ❌ **{p.get('port', '?')}**: {p.get('rejection_reason', '')}")

        # Raw JSON toggle
        with st.expander("View Raw Manifest JSON"):
            # Remove MongoDB _id for JSON display
            display_doc = {k: v for k, v in log_doc.items() if k != "_id"}
            st.json(display_doc)


if __name__ == "__main__":
    main()
