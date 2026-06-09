"""
agents/root_agent.py
---------------------
The Root Agent — autonomous logistics orchestrator.

Tool inventory:
  ── via MongoDB MCP Server (MCP mode only) ──────────────────────────────────
  find            → query vessels, ports, regulations collections (READ)
  insert-many     → write rerouting_logs (WRITE, falls back to save_rerouting_result)

  ── via Python FunctionTools (always registered) ────────────────────────────
  find_closest_alternative_ports(vessel_id, closed_port_name, limit)
      — haversine ranking of candidate ports, excludes closed port

  check_port_compliance_batch(cargo_name, port_names, session_id)
      — per-port Gemini compliance check; appends to compliance_checks in MongoDB

  calculate_route_cost(distance_km, cargo_value_usd, tariff_percent)
      — deterministic fuel + tariff math

  build_rerouting_manifest(...)
      — assembles the approved manifest dict with alternatives

  build_escalation_record(...)
      — assembles the escalation dict when all ports fail

  save_rerouting_result(build_result)
      — pymongo write fallback: inserts manifest/record into rerouting_logs
      — used when MongoDB MCP insert-many is unavailable (e.g. local dev without Docker)

  ── via Python FunctionTools (fallback mode only — mongo_toolset is None) ───
  find_vessel_by_id(vessel_id)
      — pymongo read fallback: fetches the vessel document directly
      — registered ONLY when mongo_toolset is None so the agent never hallucinates
        a non-existent tool (fixes the 'mongo_find not found' crash in local dev)

Modes:
  MCP mode     (mongo_toolset provided) — uses MongoDB MCP `find` for reads,
               `insert-many` for writes (with save_rerouting_result as fallback).
               Agent is given ROOT_AGENT_SYSTEM_PROMPT_MCP.

  Fallback mode (mongo_toolset is None) — registers find_vessel_by_id so Phase A
               has a real callable tool; uses save_rerouting_result for writes.
               Agent is given ROOT_AGENT_SYSTEM_PROMPT_FALLBACK.
"""

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool

from agents.compliance_agent import check_port_compliance_batch
from config import GEMINI_MODEL, MONGO_URI, MONGO_DB_NAME
from pymongo import MongoClient

from mcp_servers.rules_server import (
    find_closest_alternative_ports,
    calculate_route_cost,
    build_rerouting_manifest,
    build_escalation_record,
)


# ── save_rerouting_result (pymongo write fallback) ────────────────────────────

def save_rerouting_result(build_result: dict) -> dict:
    """
    Save the final rerouting manifest or escalation record to MongoDB.

    This is the WRITE fallback used when the MongoDB MCP insert-many tool
    is not available (e.g. running locally without Docker).

    In production, the agent SHOULD call insert-many via MongoDB MCP first.
    Only call this function if insert-many returned an error or is unavailable.

    Call this as the LAST step, passing the COMPLETE dict returned by
    build_rerouting_manifest or build_escalation_record.
    Do NOT pre-extract the inner "manifest" or "record" key — this function
    handles that automatically.

    Args:
        build_result: the full dict from build_rerouting_manifest
                      (contains {"status": "VALID", "manifest": {...}})
                      OR from build_escalation_record
                      (contains {"status": "VALID", "record": {...}})

    Returns:
        {"status": "SAVED", "run_id": "..."}  on success
        {"status": "ERROR", "message": "..."}  on failure
    """
    try:
        # Extract inner document automatically
        if "manifest" in build_result:
            doc = build_result["manifest"]
        elif "record" in build_result:
            doc = build_result["record"]
        else:
            doc = build_result

        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        db     = client[MONGO_DB_NAME]
        db.rerouting_logs.insert_one(doc)
        client.close()

        return {
            "status":     "SAVED",
            "run_id":     doc.get("run_id", "unknown"),
            "collection": "rerouting_logs"
        }

    except Exception as exc:
        return {"status": "ERROR", "message": str(exc)}


# ── find_vessel_by_id (pymongo read fallback) ─────────────────────────────────

def find_vessel_by_id(vessel_id: str) -> dict:
    """
    Look up a vessel document from MongoDB by its _id.

    This is the READ fallback used when the MongoDB MCP toolset is not available
    (e.g. running locally without npx / Docker). In production, the agent uses
    the MongoDB MCP `find` tool instead — this function is only registered when
    mongo_toolset is None.

    Args:
        vessel_id: the vessel's _id string (e.g. "6a25616cc658035506619a7b")

    Returns:
        The full vessel document on success:
          {
            "_id": "...",
            "vessel_name": "...",
            "cargo": {"type": "...", "value_usd": ...},
            "original_port": "...",
            "coordinates": {"lat": ..., "lng": ...}
          }
        On failure:
          {"error": "<message>"}
    """
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        db     = client[MONGO_DB_NAME]
        doc    = db.vessels.find_one({"_id": vessel_id})
        client.close()

        if doc is None:
            return {"error": f"Vessel '{vessel_id}' not found in MongoDB."}

        # Convert ObjectId to string if necessary (shouldn't be needed for string IDs)
        if "_id" in doc and not isinstance(doc["_id"], str):
            doc["_id"] = str(doc["_id"])

        return doc

    except Exception as exc:
        return {"error": str(exc)}


# ── System Prompt ─────────────────────────────────────────────────────────────
ROOT_AGENT_SYSTEM_PROMPT_MCP = """
You are an autonomous maritime logistics optimization agent.
A port disruption has occurred. Your mission: evaluate all candidate alternative
ports, find every legally compliant option, calculate costs for each, and select
the mathematically optimal (cheapest) rerouting solution.

You do not stop at the first approved port. You evaluate all N candidates.
This gives the shipping company full visibility and the best possible outcome.

════════════════════════════════════════════════
PHASE A — DISCOVERY  (uses MongoDB MCP Server)
════════════════════════════════════════════════
You will receive a disruption alert containing:
  • Main Session ID  — pass this to the manifest/escalation tools
  • Subsession ID    — pass this as 'session_id' to compliance batch and pass to manifest/escalation
  • Vessel ID        — use this to query the vessel document
  • Closed Port      — the port that is no longer available
  • N                — number of candidate ports to evaluate

1. Call the MongoDB MCP `find` tool to look up the vessel:
   {
     "database":   "LogisticsDB",
     "collection": "vessels",
     "filter":     {"_id": "<vessel_id>"}
   }

   The MongoDB MCP server returns the real document stored in Atlas.
   From the returned document, extract:
     • vessel_name
     • cargo.type       ← this is your cargo_name
     • cargo.value_usd  ← needed for cost calculations
     • original_port    ← the port that was disrupted
     • coordinates      ← lat and lng (for reference only; ranking is done by the tool)

════════════════════════════════════════════════
PHASE B — RANKING
════════════════════════════════════════════════
2. Call find_closest_alternative_ports with:
     • vessel_id:        the vessel's _id
     • closed_port_name: the name of the closed port
     • limit:            N (from the alert, default 3 if not specified)

   This returns a JSON string — a list of candidate ports sorted closest first.
   Extract the list of port names for the next step.

════════════════════════════════════════════════
PHASE C — BATCH COMPLIANCE
════════════════════════════════════════════════
3. Call check_port_compliance_batch EXACTLY ONCE with:
     • cargo_name: the vessel's cargo.type string exactly as stored
     • port_names: the list of all N port name strings from Phase B
     • session_id: the Subsession ID from the alert, exactly as given

   This tool returns a list of verdict dicts. Each dict contains:
     port_name, status ("APPROVED" or "REJECTED"), reasoning,
     cited_section, cited_page, tariff_percent, operational_consequence

4. Separate the results:
     • approved_ports: all dicts where status == "APPROVED"
     • rejected_ports: all dicts where status == "REJECTED"

   If approved_ports is empty → skip to Phase D (Escalation path).

════════════════════════════════════════════════
PHASE D — COST OPTIMIZATION (Approved ports exist)
════════════════════════════════════════════════
5. For EVERY port in approved_ports:
   a. Find its distance_km in the Phase B ranked list (match by port name)
   b. Call calculate_route_cost with: distance_km, cargo_value_usd, tariff_percent.

6. After ALL approved ports have been costed:
   a. Compare total_cost_usd across all of them
   b. The port with the LOWEST total_cost_usd is the WINNER
   c. All other approved ports become alternative_approved_ports

7. Call build_rerouting_manifest with the WINNER's data (SHOULD BE CALLED IF THERE ARE
   APPROVED PORTS! DON'T SKIP. DON'T LEAVE ANY PARAMETER EMPTY.):
     vessel_id, vessel_name, original_port, disruption_reason,
     approved_port (winner's port name),
     distance_km, fuel_cost_usd, tariff_cost_usd, total_cost_usd,
     compliance_reasoning, cited_section,
     rejected_ports (the full list of rejected verdict dicts),
     alternative_approved_ports (other approved ports with their costs, SHOULD BE FILLED),
     agent_reasoning_summary (why this port won over the alternatives),
     main_session_id, subsession_id

8. Write the manifest to MongoDB using the MCP `insert-many` tool:
   {
     "database":   "LogisticsDB",
     "collection": "rerouting_logs",
     "documents":  [ <the inner "manifest" object from build_rerouting_manifest> ]
   }
   Note: extract the "manifest" key from build_rerouting_manifest's return value
   before passing it — do NOT nest the entire {"status":"VALID","manifest":{...}} wrapper.

   If insert-many returns an error, fall back to save_rerouting_result and pass it
   the complete build_rerouting_manifest return value (including the wrapper).

════════════════════════════════════════════════
PHASE D — ESCALATION (No approved ports)
════════════════════════════════════════════════
9. Call build_escalation_record with:
     vessel_id, vessel_name, original_port, disruption_reason,
     escalation_reason, ports_tried, agent_reasoning_summary,
     main_session_id, subsession_id

10. Write the record to MongoDB using the MCP `insert-many` tool:
    {
      "database":   "LogisticsDB",
      "collection": "rerouting_logs",
      "documents":  [ <the inner "record" object from build_escalation_record> ]
    }
    If insert-many returns an error, fall back to save_rerouting_result.

════════════════════════════════════════════════
ABSOLUTE RULES
════════════════════════════════════════════════
• Every MongoDB MCP tool call MUST include database: "LogisticsDB".
• Every MongoDB filter argument must be a JSON object, NEVER a string.
  Use {"_id": "value"}, NOT "_id: \"value\"" (which is a string).
• Call insert-many EXACTLY ONCE. It ends the task. Do not call it again.
• build_rerouting_manifest returns its output under a "manifest" key.
  build_escalation_record returns its output under a "record" key.
  Extract the inner object before passing to insert-many.
• The MongoDB MCP server handles ALL reads (vessels, ports, regulations).
  Python FunctionTools handle business logic (ranking, compliance, cost, assembly).
"""

# ── Fallback system prompt (no MCP — uses find_vessel_by_id instead) ──────────
ROOT_AGENT_SYSTEM_PROMPT_FALLBACK = """
You are an autonomous maritime logistics optimization agent.
A port disruption has occurred. Your mission: evaluate all candidate alternative
ports, find every legally compliant option, calculate costs for each, and select
the mathematically optimal (cheapest) rerouting solution.

You do not stop at the first approved port. You evaluate all N candidates.
This gives the shipping company full visibility and the best possible outcome.

════════════════════════════════════════════════
PHASE A — DISCOVERY  (uses find_vessel_by_id)
════════════════════════════════════════════════
You will receive a disruption alert containing:
  • Main Session ID  — pass this to the manifest/escalation tools
  • Subsession ID    — pass this as 'session_id' to compliance batch and pass to manifest/escalation
  • Vessel ID        — use this to look up the vessel document
  • Closed Port      — the port that is no longer available
  • N                — number of candidate ports to evaluate

1. Call find_vessel_by_id with:
     vessel_id: the Vessel ID from the alert

   From the returned document, extract:
     • vessel_name
     • cargo.type       ← this is your cargo_name
     • cargo.value_usd  ← needed for cost calculations
     • original_port    ← the port that was disrupted
     • coordinates      ← lat and lng (for reference only; ranking is done by the tool)

   If the result contains an "error" key, report the error and stop.

════════════════════════════════════════════════
PHASE B — RANKING
════════════════════════════════════════════════
2. Call find_closest_alternative_ports with:
     • vessel_id:        the vessel's _id
     • closed_port_name: the name of the closed port
     • limit:            N (from the alert, default 3 if not specified)

   This returns a JSON string — a list of candidate ports sorted closest first.
   Extract the list of port names for the next step.

════════════════════════════════════════════════
PHASE C — BATCH COMPLIANCE
════════════════════════════════════════════════
3. Call check_port_compliance_batch EXACTLY ONCE with:
     • cargo_name: the vessel's cargo.type string exactly as stored
     • port_names: the list of all N port name strings from Phase B
     • session_id: the Subsession ID from the alert, exactly as given

   This tool returns a list of verdict dicts. Each dict contains:
     port_name, status ("APPROVED" or "REJECTED"), reasoning,
     cited_section, cited_page, tariff_percent, operational_consequence

4. Separate the results:
     • approved_ports: all dicts where status == "APPROVED"
     • rejected_ports: all dicts where status == "REJECTED"

   If approved_ports is empty → skip to Phase D (Escalation path).

════════════════════════════════════════════════
PHASE D — COST OPTIMIZATION (Approved ports exist)
════════════════════════════════════════════════
5. For EVERY port in approved_ports:
   a. Find its distance_km in the Phase B ranked list (match by port name)
   b. Call calculate_route_cost with: distance_km, cargo_value_usd, tariff_percent.

6. After ALL approved ports have been costed:
   a. Compare total_cost_usd across all of them
   b. The port with the LOWEST total_cost_usd is the WINNER
   c. All other approved ports become alternative_approved_ports

7. Call build_rerouting_manifest with the WINNER's data (SHOULD BE CALLED IF THERE ARE
   APPROVED PORTS! DON'T SKIP. DON'T LEAVE ANY PARAMETER EMPTY.):
     vessel_id, vessel_name, original_port, disruption_reason,
     approved_port (winner's port name),
     distance_km, fuel_cost_usd, tariff_cost_usd, total_cost_usd,
     compliance_reasoning, cited_section,
     rejected_ports (the full list of rejected verdict dicts),
     alternative_approved_ports (other approved ports with their costs, SHOULD BE FILLED),
     agent_reasoning_summary (why this port won over the alternatives),
     main_session_id, subsession_id

8. Save the manifest using save_rerouting_result, passing it the COMPLETE dict
   returned by build_rerouting_manifest (including the wrapper).

════════════════════════════════════════════════
PHASE D — ESCALATION (No approved ports)
════════════════════════════════════════════════
9. Call build_escalation_record with:
     vessel_id, vessel_name, original_port, disruption_reason,
     escalation_reason, ports_tried, agent_reasoning_summary,
     main_session_id, subsession_id

10. Save using save_rerouting_result, passing it the COMPLETE dict
    returned by build_escalation_record (including the wrapper).

════════════════════════════════════════════════
ABSOLUTE RULES
════════════════════════════════════════════════
• Call find_vessel_by_id ONCE at the start. If it returns {"error": ...}, stop.
• Call save_rerouting_result EXACTLY ONCE. It ends the task. Do not call it again.
• build_rerouting_manifest returns its output under a "manifest" key.
  build_escalation_record returns its output under a "record" key.
  Pass the COMPLETE wrapper dict to save_rerouting_result — it handles extraction.
• Python FunctionTools handle ALL reads and business logic in this mode.
"""


# ── Agent Factory ─────────────────────────────────────────────────────────────

def build_root_agent(mongo_toolset=None) -> LlmAgent:
    """
    Build and return the Root Agent in one of two modes:

    MCP mode (mongo_toolset provided):
      - Uses MongoDB MCP `find` for vessel/port/regulation reads (shows MCP integration).
      - Uses MongoDB MCP `insert-many` for writes, with save_rerouting_result as fallback.
      - System prompt instructs agent to call the MCP `find` tool.

    Fallback mode (mongo_toolset is None, e.g. local dev without npx/Docker):
      - Registers find_vessel_by_id as a Python FunctionTool for vessel reads.
      - Uses save_rerouting_result for all writes.
      - System prompt instructs agent to call find_vessel_by_id instead of mongo find.
      - No hallucinated tool names — every tool in the prompt is actually registered.
    """
    base_function_tools = [
        FunctionTool(find_closest_alternative_ports),
        FunctionTool(check_port_compliance_batch),
        FunctionTool(calculate_route_cost),
        FunctionTool(build_rerouting_manifest),
        FunctionTool(build_escalation_record),
        FunctionTool(save_rerouting_result),
    ]

    if mongo_toolset:
        # MCP mode: MCP toolset provides find + insert-many; Python tools handle logic
        all_tools = [mongo_toolset] + base_function_tools
        system_prompt = ROOT_AGENT_SYSTEM_PROMPT_MCP
    else:
        # Fallback mode: add find_vessel_by_id so Phase A has a real tool to call
        all_tools = base_function_tools + [FunctionTool(find_vessel_by_id)]
        system_prompt = ROOT_AGENT_SYSTEM_PROMPT_FALLBACK

    return LlmAgent(
        name="logistics_rerouting_agent",
        model=GEMINI_MODEL,
        instruction=system_prompt,
        tools=all_tools,
    )