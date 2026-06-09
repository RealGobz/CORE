"""
mcp_servers/rules_server.py
----------------------------
Custom FastMCP server exposing deterministic business logic tools.
The Root Agent calls these instead of doing math or assembly in Gemini's reasoning.
"""

import sys
import os
import json
import logging
import uuid
from datetime import datetime, timezone

if sys.platform == "win32":
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastmcp import FastMCP
from haversine import haversine
from pymongo import MongoClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from config import MONGO_URI, MONGO_DB_NAME

# ── Persistent MongoDB connection (shared across all tool calls) ──────────────
_mongo_client = None
_mongo_db     = None

def _get_db():
    global _mongo_client, _mongo_db
    if _mongo_client is None:
        _mongo_client = MongoClient(
            MONGO_URI,
            serverSelectionTimeoutMS=3000,
            connectTimeoutMS=3000,
            socketTimeoutMS=10000,
        )
        _mongo_db = _mongo_client[MONGO_DB_NAME]
    return _mongo_db

logging.basicConfig(level=logging.WARNING)

mcp = FastMCP(
    name="logistics-rules-server",
    instructions="Deterministic business logic for maritime supply chain rerouting."
)

# ── Global Cache for Ports (lazy initialization) ────────────────────────────

PORTS_CACHE = []
MONGODB_CONN = None
CACHE_INITIALIZED = False

def _init_cache_lazy():
    """Lazy initialization: load ports on first use, not at module import."""
    global PORTS_CACHE, MONGODB_CONN, CACHE_INITIALIZED
    
    if CACHE_INITIALIZED:
        return  # Already initialized
    
    try:
        MONGODB_CONN = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
        db = MONGODB_CONN[MONGO_DB_NAME]
        PORTS_CACHE = list(db.ports.find({}))
        CACHE_INITIALIZED = True
        logging.info(f"[MCP] Cache initialized: {len(PORTS_CACHE)} ports loaded")
    except Exception as e:
        logging.warning(f"[MCP] Cache initialization deferred (will query on-demand): {e}")
        CACHE_INITIALIZED = False  # Mark as failed so we try again next time
        PORTS_CACHE = []


@mcp.tool()
def find_closest_alternative_ports(
    vessel_id: str,
    closed_port_name: str,
    limit: int = 3
) -> str:
    """
    Find the N closest alternative ports to a given vessel.
    Uses cached ports (if available) and a MongoDB query for vessel coordinates.
    """
    try:
        # Attempt lazy cache initialization on first call
        _init_cache_lazy()
        
        # Query for vessel coordinates (lightweight, single document)
        try:
            client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
            db = _get_db()
            vessel = db.vessels.find_one({"_id": vessel_id}, {"coordinates": 1})
            client.close()
        except Exception as e:
            return json.dumps({"error": f"Could not fetch vessel coordinates: {str(e)}"})
        
        if not vessel or "coordinates" not in vessel:
            return json.dumps({"error": f"Vessel '{vessel_id}' not found or missing coordinates."})

        vessel_coords = (vessel["coordinates"]["lat"], vessel["coordinates"]["lng"])

        # Use cached ports if available
        candidate_ports = []
        if PORTS_CACHE:
            # Use the pre-loaded cache
            for port in PORTS_CACHE:
                if port.get("name") == closed_port_name:
                    continue
                if "coordinates" in port:
                    port_coords = (port["coordinates"]["lat"], port["coordinates"]["lng"])
                    distance = round(haversine(vessel_coords, port_coords), 2)
                    candidate_ports.append({
                        "name":        port.get("name"),
                        "country":     port.get("country"),
                        "distance_km": distance
                    })
        else:
            # Fallback: query ports from database if cache is empty
            try:
                client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
                db = client[MONGO_DB_NAME]
                for port in db.ports.find({}):
                    if port.get("name") == closed_port_name:
                        continue
                    if "coordinates" in port:
                        port_coords = (port["coordinates"]["lat"], port["coordinates"]["lng"])
                        distance = round(haversine(vessel_coords, port_coords), 2)
                        candidate_ports.append({
                            "name":        port.get("name"),
                            "country":     port.get("country"),
                            "distance_km": distance
                        })
                client.close()
            except Exception as e:
                return json.dumps({"error": f"Could not query ports: {str(e)}"})

        candidate_ports.sort(key=lambda x: x["distance_km"])
        return json.dumps(candidate_ports[:limit])

    except Exception as exc:
        logging.error("find_closest_alternative_ports error: %s", exc)
        return json.dumps({"error": str(exc)})


@mcp.tool()
def calculate_route_cost(
    distance_km: float,
    cargo_value_usd: float,
    tariff_percent: float
) -> dict:
    """Calculate the full rerouting cost for a vessel."""
    fuel_cost   = round(distance_km * 80.0, 2)
    tariff_cost = round(cargo_value_usd * (tariff_percent / 100.0), 2)
    total_cost  = round(fuel_cost + tariff_cost, 2)
    return {
        "fuel_cost_usd":   fuel_cost,
        "tariff_cost_usd": tariff_cost,
        "total_cost_usd":  total_cost
    }


@mcp.tool()
def build_rerouting_manifest(
    vessel_id: str,
    vessel_name: str,
    original_port: str,
    disruption_reason: str,
    approved_port: str,
    distance_km: float,
    fuel_cost_usd: float,
    tariff_cost_usd: float,
    total_cost_usd: float,
    compliance_reasoning: str,
    cited_section: str,
    rejected_ports: list,
    alternative_approved_ports: list,
    agent_reasoning_summary: str,
    main_session_id: str,
    subsession_id: str
) -> dict:
    """
    Assemble all calculated data into a valid rerouting manifest.
    Call this only for the winning (cheapest approved) port.
    """
    return {
        "status": "VALID",
        "manifest": {
            "run_id":                     str(uuid.uuid4()),
            "main_session_id":            main_session_id,
            "subsession_id":              subsession_id,
            "vessel_id":                  vessel_id,
            "vessel_name":                vessel_name,
            "original_port":              original_port,
            "disruption_reason":          disruption_reason,
            "result": {
                "status":                 "RESOLVED",
                "approved_port":          approved_port,
                "distance_km":            distance_km,
                "fuel_cost_usd":          fuel_cost_usd,
                "tariff_cost_usd":        tariff_cost_usd,
                "total_cost_usd":         total_cost_usd,
                "compliance_approval": {
                    "reasoning":          compliance_reasoning,
                    "cited_section":      cited_section
                }
            },
            "rejected_ports":             rejected_ports,
            "alternative_approved_ports": alternative_approved_ports,
            "agent_reasoning_summary":    agent_reasoning_summary,
            "human_decision":             "PENDING",
            "timestamp":                  datetime.now(timezone.utc).isoformat()
        }
    }


@mcp.tool()
def build_escalation_record(
    vessel_id: str,
    vessel_name: str,
    original_port: str,
    disruption_reason: str,
    escalation_reason: str,
    ports_tried: list,
    agent_reasoning_summary: str,
    main_session_id: str,
    subsession_id: str
) -> dict:
    """
    Build an escalation record when no compliant port was found.
    Call this only when ALL candidate ports were REJECTED.
    """
    return {
        "status": "VALID",
        "record": {
            "run_id":                     str(uuid.uuid4()),
            "main_session_id":            main_session_id,
            "subsession_id":              subsession_id,
            "vessel_id":                  vessel_id,
            "vessel_name":                vessel_name,
            "original_port":              original_port,
            "disruption_reason":          disruption_reason,
            "result": {
                "status":                 "ESCALATED",
                "escalation_reason":      escalation_reason,
                "ports_tried":            ports_tried
            },
            "agent_reasoning_summary":    agent_reasoning_summary,
            "human_decision":             "REQUIRED",
            "timestamp":                  datetime.now(timezone.utc).isoformat()
        }
    }


if __name__ == "__main__":
    transport = os.getenv("FASTMCP_TRANSPORT", "stdio")
    port_num  = int(os.getenv("PORT", "8080"))
    if transport == "sse":
        mcp.run(transport="sse", host="0.0.0.0", port=port_num)
    else:
        mcp.run(transport="stdio")