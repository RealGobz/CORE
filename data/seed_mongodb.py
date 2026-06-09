"""
data/seed_mongodb.py
---------------------
Run this ONCE before starting the agents.
Wipes and recreates all collections with clean demo data.

Collections created:
  vessels          — ship info, cargo, coordinates
  ports            — candidate alternative ports
  disruptions      — active disruption trigger
  regulations      — port-specific regulatory text (read by compliance tool)
  cargo_constraints — cargo type profiles (NEW in v2, read by compliance tool)
  rerouting_logs   — written by the agent at runtime, read by the UI

Usage:
    python data/seed_mongodb.py
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pymongo import MongoClient, ASCENDING
from config import MONGO_URI, MONGO_DB_NAME
from datetime import datetime, timezone

# ── Connect ───────────────────────────────────────────────────────────────────
client = MongoClient(MONGO_URI)
db = client[MONGO_DB_NAME]

print(f"🔌 Connected to MongoDB — database: {MONGO_DB_NAME}")

# ── Drop and recreate all collections (clean slate for demo) ──────────────────
ALL_COLLECTIONS = [
    "vessels", "ports", "disruptions",
    "rerouting_logs", "regulations", "cargo_constraints"
]
for col in ALL_COLLECTIONS:
    db.drop_collection(col)
    print(f"   🗑  Dropped: {col}")

# ── 1. vessels ────────────────────────────────────────────────────────────────
db.vessels.insert_one({
    "_id": "MV_ATLAS_001",
    "vessel_name": "MV Atlas",
    "coordinates": {"lat": 33.754, "lng": -118.216},
    "cargo": {
        "type": "Pharmaceuticals",        # ← this exact string goes to check_port_compliance
        "temperature_sensitive": True,
        "containers": 450,
        "value_usd": 12_000_000
    },
    "original_port": "Port of Long Beach",
    "status": "DISRUPTED"
})
print("✅ Seeded: vessels")

# ── 2. ports ──────────────────────────────────────────────────────────────────
# Field "name" is what the Root Agent extracts and passes to check_port_compliance.
# It must match the "port_name" field in the regulations collection exactly.
db.ports.insert_many([
    {
        "_id": "PORT_OAKLAND",
        "name": "Port of Oakland",
        "coordinates": {"lat": 37.796, "lng": -122.282},
        "country": "USA"
    },
    {
        "_id": "PORT_SEATTLE",
        "name": "Port of Seattle",
        "coordinates": {"lat": 47.601, "lng": -122.335},
        "country": "USA"
    },
    {
        "_id": "PORT_MANZANILLO",
        "name": "Port of Manzanillo",
        "coordinates": {"lat": 19.049, "lng": -104.318},
        "country": "Mexico"
    }
])
print("✅ Seeded: ports")

# ── 3. disruptions ────────────────────────────────────────────────────────────
db.disruptions.insert_one({
    "_id": "DISRUPTION_001",
    "closed_port": "Port of Long Beach",
    "reason": "Labor Strike",
    "affected_vessels": ["MV_ATLAS_001"],
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "active": True
})
print("✅ Seeded: disruptions")

# ── 4. cargo_constraints (NEW in v2) ─────────────────────────────────────────
# Fetched by the compliance tool Python wrapper before calling the LLM.
# Gives the compliance LLM a rich cargo profile so it can make correct decisions
# when reading port regulations — e.g. knowing the cargo is temperature-sensitive
# lets it correctly flag quarantine clauses as a spoilage risk.
db.cargo_constraints.insert_many([
    {
        "_id": "CARGO_PHARMA",
        "cargo_type": "Pharmaceuticals",
        "temperature_sensitive": True,
        "requires_cold_chain": True,
        "cold_chain_temp_celsius": "-20 to +8",
        "max_ambient_exposure_hours": 2,
        "hazmat_class": None,
        "description": (
            "Temperature-controlled pharmaceutical products including biologics, "
            "vaccines, and medications requiring continuous refrigeration throughout "
            "the supply chain. Cold chain must be maintained from origin to final delivery."
        ),
        "spoilage_risk": (
            "CRITICAL — any interruption to cold chain or exposure to ambient "
            "temperatures beyond 2 hours causes irreversible and complete cargo loss. "
            "A 5-day quarantine in ambient storage renders the entire shipment unsellable."
        ),
        "handling_requirements": [
            "Continuous refrigeration required at all times",
            "Cannot tolerate quarantine periods in ambient or standard storage",
            "Priority unloading and immediate transfer to cold storage required on arrival",
            "FDA pre-clearance documentation required at US ports"
        ],
        "estimated_value_per_container_usd": 26_667
    },
    {
        "_id": "CARGO_ELECTRONICS",
        "cargo_type": "Electronics",
        "temperature_sensitive": False,
        "requires_cold_chain": False,
        "cold_chain_temp_celsius": None,
        "max_ambient_exposure_hours": None,
        "hazmat_class": None,
        "description": "Consumer and industrial electronics including semiconductors and devices.",
        "spoilage_risk": "LOW — standard handling is sufficient",
        "handling_requirements": [
            "Avoid moisture exposure",
            "Standard customs inspection applies"
        ],
        "estimated_value_per_container_usd": 85_000
    }
])
print("✅ Seeded: cargo_constraints")

# ── 5. regulations ────────────────────────────────────────────────────────────
# Fetched by the compliance tool Python wrapper.
# The "port_name" field must match exactly what is stored in the ports collection.
# The "text" field is the full regulatory document passed to the compliance LLM.

OAKLAND_TEXT = """
PORT OF OAKLAND — PHARMACEUTICAL IMPORT REGULATIONS
Effective Date: January 1, 2024
Document Reference: OAK-REG-2024-PHARMA

SECTION 1 — SCOPE
These regulations govern the importation of pharmaceutical products,
medical supplies, biologics, and temperature-controlled medical cargo
arriving at the Port of Oakland, California, USA.

SECTION 2 — STANDARD IMPORT PROCEDURES, Page 3
2.1 Standard customs clearance applies to all general cargo. Clearance
is typically issued within 48 hours of vessel arrival for non-restricted
goods.
2.2 All cargo must be accompanied by a valid bill of lading and commercial
invoice. Failure to provide documentation results in immediate hold.

SECTION 3 — TARIFF SCHEDULE, Page 5
3.1 General merchandise: 2.5% ad valorem tariff on declared cargo value.
3.2 Electronics and technology goods: 7.5% ad valorem tariff.
3.3 Pharmaceutical and medical goods: 3% ad valorem tariff.

SECTION 4 — PHARMACEUTICAL AND MEDICAL SUPPLY REQUIREMENTS, Page 7
4.1 All pharmaceutical imports must be pre-registered with the FDA
Regional Office for the Port of Oakland prior to vessel arrival.
4.2 All pharmaceutical and medical supply imports arriving at the Port
of Oakland are subject to a mandatory 5-day quarantine inspection by
the FDA Port Authority. This inspection is required regardless of the
origin country or pre-clearance status.
4.3 Temperature-controlled goods including vaccines, biologics, and
temperature-sensitive medications will be held in standard ambient
storage during the quarantine period pending inspection. The Port
Authority assumes no liability for spoilage of temperature-sensitive
cargo during the quarantine window.
4.4 Importers must arrange and fund their own temperature monitoring
equipment during quarantine. Port Authority standard facilities do not
include cold storage.

SECTION 5 — RESTRICTED AND PROHIBITED GOODS, Page 9
5.1 Unapproved pharmaceutical compounds are subject to immediate
seizure without compensation.
5.2 Biologics must carry USDA-APHIS certification.
"""

SEATTLE_TEXT = """
PORT OF SEATTLE — PHARMACEUTICAL IMPORT REGULATIONS
Effective Date: March 1, 2024
Document Reference: SEA-REG-2024-PHARMA

SECTION 1 — SCOPE
These regulations govern the importation of pharmaceutical products,
medical supplies, biologics, and related cargo arriving at the Port
of Seattle, Washington, USA.

SECTION 2 — IMPORT TARIFF SCHEDULE, Page 3
2.1 Pharmaceutical goods imported through the Port of Seattle are
subject to a standard 5% ad valorem tariff on declared cargo value.
This tariff applies uniformly to all pharmaceutical categories.
2.2 No additional port surcharge applies to pharmaceutical cargo.
2.3 Tariff payment is due within 30 days of port clearance.

SECTION 3 — CLEARANCE PROCEDURES, Page 4
3.1 Pre-certified pharmaceutical shipments from FDA-registered
manufacturers are eligible for expedited clearance.
3.2 No quarantine or additional health inspection is required for
pre-certified pharmaceutical shipments from FDA-registered sources.
3.3 Port clearance is typically granted within 24 hours of arrival
for pre-certified cargo.
3.4 Temperature-controlled cargo receives priority handling. The Port
of Seattle operates certified cold-storage facilities at Pier 46
capable of maintaining temperatures between -20°C and +8°C.

SECTION 4 — DOCUMENTATION REQUIREMENTS, Page 6
4.1 Standard bill of lading and commercial invoice required.
4.2 FDA registration certificate for the manufacturing facility.
4.3 Certificate of Analysis for each product batch.

SECTION 5 — RESTRICTED GOODS, Page 8
5.1 Unapproved experimental compounds require prior written FDA
authorization before port entry will be granted.
"""

MANZANILLO_TEXT = """
PORT OF MANZANILLO — PHARMACEUTICAL IMPORT REGULATIONS
Effective Date: July 1, 2023
Document Reference: MZT-REG-2023-PHARMA

SECTION 1 — SCOPE
These regulations govern pharmaceutical and medical supply imports
arriving at the Port of Manzanillo, Colima, Mexico.

SECTION 2 — TARIFF SCHEDULE, Page 2
2.1 All pharmaceutical imports are subject to an 18% ad valorem
import tariff under Mexico's General Import Tariff schedule.
2.2 An additional 16% VAT (IVA) is assessed on the CIF value plus
the import tariff, bringing the effective tax burden to approximately
34% of declared cargo value.
2.3 Pharmaceutical goods from non-USMCA countries are subject to the
full tariff schedule without preferential treatment.

SECTION 3 — SANITARY REGISTRATION, Page 4
3.1 All pharmaceutical products must carry an active COFEPRIS
(Federal Commission for Protection against Sanitary Risk) registration
number prior to port entry.
3.2 Products without valid COFEPRIS registration are subject to
immediate seizure and destruction. No appeals process is available
at the port level.
3.3 Temperature-sensitive goods are subject to a mandatory 72-hour
sanitary inspection period during which cold-chain integrity cannot
be guaranteed by port authorities.

SECTION 4 — PROHIBITED GOODS, Page 6
4.1 Biologics and vaccines not listed on Mexico's National Formulary
are prohibited from entry without a special COFEPRIS waiver,
which requires 45-90 business days to process.
"""

db.regulations.insert_many([
    {
        "_id": "REG_OAKLAND_PHARMA",
        "port_name": "Port of Oakland",      # must match ports.name exactly
        "cargo_category": "Pharmaceuticals",
        "document_reference": "OAK-REG-2024-PHARMA",
        "text": OAKLAND_TEXT
    },
    {
        "_id": "REG_SEATTLE_PHARMA",
        "port_name": "Port of Seattle",
        "cargo_category": "Pharmaceuticals",
        "document_reference": "SEA-REG-2024-PHARMA",
        "text": SEATTLE_TEXT
    },
    {
        "_id": "REG_MANZANILLO_PHARMA",
        "port_name": "Port of Manzanillo",
        "cargo_category": "Pharmaceuticals",
        "document_reference": "MZT-REG-2023-PHARMA",
        "text": MANZANILLO_TEXT
    }
])
print("✅ Seeded: regulations")

# ── 6. Indexes ────────────────────────────────────────────────────────────────
db.disruptions.create_index([("active", ASCENDING)])
db.rerouting_logs.create_index([("vessel_id", ASCENDING), ("timestamp", ASCENDING)])
db.regulations.create_index([("port_name", ASCENDING), ("cargo_category", ASCENDING)])
db.cargo_constraints.create_index([("cargo_type", ASCENDING)])
print("✅ Created indexes")

print("\n🚀 MongoDB seeding complete. All collections ready.")
print(f"   Collections: {', '.join(ALL_COLLECTIONS)}")
client.close()
