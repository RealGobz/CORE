"""
agents/compliance_agent.py
---------------------------
Compliance Processing Unit.

Single-port:  check_port_compliance(cargo_name, port_name) -> dict
Batch:        check_port_compliance_batch(cargo_name, port_names, session_id) -> list

The batch function loops port-by-port (isolated Gemini call per port),
appends results to the session's compliance_checks field in MongoDB via $push,
and returns the full list to the Root Agent.

MongoDB writes: $push to compliance_checks (additive — never overwrites).
"""

import json
import logging
from datetime import datetime, timezone
from pymongo import MongoClient
from google import genai
from google.genai import types as genai_types

from config import MONGO_URI, MONGO_DB_NAME, GEMINI_MODEL

log = logging.getLogger("compliance")


COMPLIANCE_SYSTEM_PROMPT = """
You are a trade compliance parsing engine.

You will receive exactly two data blocks:
  1. CARGO PROFILE   — JSON describing the cargo being shipped
  2. PORT REGULATIONS — the official regulatory text for the destination port

YOUR ONLY JOB:
Compare the cargo profile against the port regulations.
Determine whether the cargo is APPROVED or REJECTED.
Output a single JSON object. Nothing else.

OUTPUT SCHEMA — every field required:
{
  "status": "APPROVED",
  "reasoning": "Plain English citing the exact regulation that drove the decision",
  "cited_section": "Section X.Y",
  "cited_page": 7,
  "tariff_percent": 5.0,
  "operational_consequence": "Practical impact on the vessel and cargo"
}

DECISION RULES — in order:
1. If PORT REGULATIONS block says NO REGULATIONS FOUND or is empty:
   → REJECTED. Reasoning: "No regulations found for [cargo type] at this port.
     Compliance cannot be verified. Port rejected for safety."

2. If cargo temperature_sensitive is true AND regulations mention
   quarantine, mandatory hold, inspection hold, ambient storage,
   uncontrolled storage, or standard storage during inspection:
   → REJECTED. State explicitly that cold chain breaks and cargo spoils.

3. If no disqualifying conditions: APPROVED.
   Set tariff_percent to the numeric rate in regulations (0.0 if none).

TYPE ENFORCEMENT:
- cited_page: integer  (7, not "Page 7")
- tariff_percent: float (5.0, not "5%" or null)
- status: exactly "APPROVED" or "REJECTED"

ABSOLUTE: Output ONLY the JSON. No markdown. No preamble. Nothing else.
"""


# ── Internal helpers ──────────────────────────────────────────────────────────

def _fetch_compliance_data(cargo_name: str, port_name: str) -> tuple:
    """
    Fetch cargo constraints and port regulations from MongoDB.
    Queries cargo-specific regulations first; falls back to any regulation
    for that port if no cargo-specific match exists.
    """
    client    = MongoClient(MONGO_URI, serverSelectionTimeoutMS=6000)
    db        = client[MONGO_DB_NAME]
    cargo_doc = db.cargo_constraints.find_one({"cargo_type": cargo_name}, {"_id": 0})

    # Try cargo-specific regulation first
    reg_doc = db.regulations.find_one(
        {"port_name": port_name, "cargo_category": cargo_name},
        {"_id": 0, "text": 1}
    )

    # Fallback: any regulation for this port
    if not reg_doc:
        reg_doc = db.regulations.find_one(
            {"port_name": port_name},
            {"_id": 0, "text": 1}
        )

    client.close()

    if reg_doc and reg_doc.get("text", "").strip():
        reg_text = reg_doc["text"]
    else:
        # Make the "no data" message explicitly cargo-specific so the LLM
        # gives an informative reason rather than generic rejection language.
        reg_text = (
            f"NO REGULATIONS FOUND FOR CARGO TYPE '{cargo_name}' "
            f"AT PORT '{port_name}'. "
            f"Compliance cannot be verified for this cargo category at this port."
        )

    return cargo_doc, reg_text


def _call_compliance_llm(cargo_name: str, port_name: str, cargo_doc, reg_text: str) -> dict:
    """Assemble prompt, call Gemini with forced JSON output, return parsed dict."""
    cargo_profile_text = (
        json.dumps(cargo_doc, indent=2)
        if cargo_doc
        else f'{{"cargo_type": "{cargo_name}", "note": "No constraints document found"}}'
    )

    assembled_input = (
        f"CARGO PROFILE:\n{cargo_profile_text}\n\n"
        f"PORT REGULATIONS — {port_name}:\n{reg_text}"
    )

    llm_client = genai.Client()  # global Vertex AI patch from runner.py
    response   = llm_client.models.generate_content(
        model=GEMINI_MODEL,
        contents=assembled_input,
        config=genai_types.GenerateContentConfig(
            system_instruction=COMPLIANCE_SYSTEM_PROMPT,
            response_mime_type="application/json"
        )
    )

    result = json.loads(response.text)
    result["tariff_percent"] = float(result.get("tariff_percent") or 0.0)
    result["cited_page"]     = int(result.get("cited_page") or 0)
    result.setdefault("operational_consequence", "")
    return result


# ── Single-port function ──────────────────────────────────────────────────────

def check_port_compliance(cargo_name: str, port_name: str) -> dict:
    """
    Compliance check for one cargo type at one port.

    Args:
        cargo_name: vessel's cargo.type (e.g. "Pharmaceuticals")
        port_name:  port name field    (e.g. "Port of Seattle")

    Returns:
        dict: status, reasoning, cited_section, cited_page,
              tariff_percent, operational_consequence
    """
    try:
        cargo_doc, reg_text = _fetch_compliance_data(cargo_name, port_name)
    except Exception as exc:
        log.error("DB fetch failed [%s]: %s", port_name, exc)
        return _fallback_rejection(port_name, f"Database unreachable: {exc}")

    try:
        result = _call_compliance_llm(cargo_name, port_name, cargo_doc, reg_text)
        log.info("Compliance | port='%s' status=%s tariff=%.1f%%",
                 port_name, result.get("status"), result["tariff_percent"])
        return result
    except Exception as exc:
        log.error("LLM call failed [%s]: %s", port_name, exc)
        return _fallback_rejection(port_name, f"LLM unavailable: {exc}")


# ── Batch function — primary tool called by Root Agent ────────────────────────

def check_port_compliance_batch(
    cargo_name: str,
    port_names: list,
    session_id: str
) -> list:
    """
    Run compliance checks for multiple ports — one isolated Gemini call per port.

    After the loop, each result is APPENDED to compliance_checks in the
    MongoDB session document via $push. This field accumulates across the
    full session lifetime (initial run + any follow-up checks in chat).

    Args:
        cargo_name:  vessel's cargo.type string (e.g. "Pharmaceuticals")
        port_names:  list of port name strings from the ranked ports list
        session_id:  sub-session ID — used to append telemetry to MongoDB

    Returns:
        list of dicts, one per port:
            port_name, status, reasoning, cited_section, cited_page,
            tariff_percent, operational_consequence, checked_at
    """
    compliance_checks = []

    for port_name in port_names:
        log.info("Batch check %d/%d: '%s' for '%s'",
                 len(compliance_checks) + 1, len(port_names), port_name, cargo_name)

        try:
            cargo_doc, reg_text = _fetch_compliance_data(cargo_name, port_name)
        except Exception as exc:
            log.error("DB fetch failed [%s]: %s", port_name, exc)
            verdict = _fallback_rejection(port_name, f"Database unreachable: {exc}")
            compliance_checks.append(verdict)
            continue

        try:
            verdict = _call_compliance_llm(cargo_name, port_name, cargo_doc, reg_text)
        except Exception as exc:
            log.error("LLM call failed [%s]: %s", port_name, exc)
            verdict = _fallback_rejection(port_name, f"LLM unavailable: {exc}")

        verdict["port_name"]  = port_name
        verdict["checked_at"] = datetime.now(timezone.utc).isoformat()

        log.info("Verdict | port='%s' status=%s tariff=%.1f%%",
                 port_name, verdict.get("status"), verdict.get("tariff_percent", 0))
        compliance_checks.append(verdict)

    # Append to MongoDB session — $push is additive, never overwrites history
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        db     = client[MONGO_DB_NAME]
        db.sessions.update_one(
            {"_id": session_id},
            {
                "$push": {"compliance_checks": {"$each": compliance_checks}},
                # Also maintain compliance_reports for backward compatibility
                # with sessions written before the field rename
                "$set":  {"compliance_reports": compliance_checks}
            },
            upsert=True
        )
        client.close()
        log.info("Appended %d compliance checks to session '%s'.",
                 len(compliance_checks), session_id)
    except Exception as exc:
        log.error("Failed to save compliance checks to session: %s", exc)

    return compliance_checks


# ── Fallback ──────────────────────────────────────────────────────────────────

def _fallback_rejection(port_name: str, reason: str) -> dict:
    """Safe REJECTED verdict when the compliance pipeline itself fails."""
    return {
        "port_name":              port_name,
        "status":                 "REJECTED",
        "reasoning":              f"Compliance check failed: {reason}. Port rejected for safety.",
        "cited_section":          "N/A",
        "cited_page":             0,
        "tariff_percent":         0.0,
        "operational_consequence": "Manual compliance review required before vessel proceeds.",
        "checked_at":             datetime.now(timezone.utc).isoformat()
    }
