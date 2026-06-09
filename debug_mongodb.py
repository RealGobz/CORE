"""
debug_mongodb.py
----------------
Debug script that uses the exact same function as the trigger agent.
Change VESSEL_ID_TO_TEST at the top and run to debug different formats.
"""

import os
import json
from pymongo import MongoClient
from bson import ObjectId
from pprint import pprint

# ── MongoDB Configuration ────────────────────────────────────────────────────

# os.load_dotenv()  # Load from .env if availables

MONGO_URI="mongodb+srv://3mar:reem2006@cluster0.fu7slfw.mongodb.net/?appName=Cluster0"
MONGO_DB_NAME="LogisticsDB"
# ── TEST VALUES ──────────────────────────────────────────────────────────────

# Change these to match your test case
MAIN_SESSION_ID = "main_20260607_160338_8aaef1"  # From the output above
VESSEL_ID_TO_TEST = "6a25616cc658035506619a7b"   # The ID you want to test

# ── Exact copy of get_subsession_compliance from runner.py ───────────────────

def get_subsession_compliance(vessel_id: str, main_session_id: str) -> dict:
    """
    Exact copy of the function used by the trigger agent.
    Retrieve the compliance_checks for the sub-session that handled a specific vessel.
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


# ── Debug Helper ──────────────────────────────────────────────────────────────

def debug_with_different_formats(vessel_id_str: str):
    """Try different formats to see which one works."""
    print(f"\n{'='*70}")
    print(f"  Testing vessel ID: {vessel_id_str}")
    print(f"  Main session: {MAIN_SESSION_ID}")
    print(f"{'='*70}")
    
    # Test 1: As string directly
    print(f"\n[1] Testing as string '{vessel_id_str}'...")
    result = get_subsession_compliance(vessel_id_str, MAIN_SESSION_ID)
    print("RAW RESULT:")
    pprint(result)
    
    # Test 2: As ObjectId
    print(f"\n[2] Testing as ObjectId...")
    try:
        oid = ObjectId(vessel_id_str)
        result = get_subsession_compliance(str(oid), MAIN_SESSION_ID)
        print("RAW RESULT:")
        pprint(result)
    except Exception as e:
        print(f"  ✗ Could not convert to ObjectId: {e}")
    
    # Test 3: List all subsessions to debug
    print(f"\n[3] Listing all subsessions under main_session_id='{MAIN_SESSION_ID}'...")
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=4000)
        db = client[MONGO_DB_NAME]
        docs = list(db.sessions.find(
            {"main_session_id": MAIN_SESSION_ID},
            {"_id": 1, "vessel_id": 1}
        ))
        client.close()
        
        if docs:
            print(f"Found {len(docs)} subsession(s):")
            pprint(docs)
        else:
            print(f"  ✗ No subsessions found for this main_session_id")
    except Exception as e:
        print(f"  ✗ Error: {e}")
        import traceback
        traceback.print_exc()


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n  MongoDB Debug Script (using exact agent function)")
    print(f"  URI: {MONGO_URI}")
    print(f"  DB:  {MONGO_DB_NAME}")
    
    # Test with different formats
    debug_with_different_formats(VESSEL_ID_TO_TEST)
    
    print(f"\n{'='*70}")
    print("  To test another ID or main_session, edit the values at the top")
    print(f"{'='*70}\n")
