"""
seed_demo_db.py — Demo data for autonomous-routing MVP
Run: python3 seed_demo_db.py
Skips documents that already exist (_id conflict) to avoid duplicates.
"""

from pymongo import MongoClient, errors
from datetime import datetime, timezone
import os, sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from config import MONGO_URI, MONGO_DB_NAME

MONGO_URI = MONGO_URI
MONGO_DB  = MONGO_DB_NAME

client = MongoClient(MONGO_URI)
db     = client[MONGO_DB]

def safe_insert(collection, docs):
    inserted = 0
    skipped  = 0
    for doc in docs:
        try:
            collection.insert_one(doc)
            inserted += 1
        except errors.DuplicateKeyError:
            skipped += 1
    print(f"  {collection.name}: {inserted} inserted, {skipped} skipped")

# ══════════════════════════════════════════════════════════════════════════════
# PORTS — 5 US West Coast cluster + 5 Southeast Asia cluster
# ══════════════════════════════════════════════════════════════════════════════

ports = [
    # ── US West Coast cluster ─────────────────────────────────────────────────
    {
        "_id": "PORT_LOS_ANGELES",
        "name": "Port of Los Angeles",
        "coordinates": {"lat": 33.7361, "lng": -118.2922},
        "country": "USA"
    },
    {
        "_id": "PORT_SAN_DIEGO",
        "name": "Port of San Diego",
        "coordinates": {"lat": 32.7157, "lng": -117.1611},
        "country": "USA"
    },
    {
        "_id": "PORT_SAN_FRANCISCO",
        "name": "Port of San Francisco",
        "coordinates": {"lat": 37.7956, "lng": -122.3934},
        "country": "USA"
    },
    {
        "_id": "PORT_SEATTLE",
        "name": "Port of Seattle",
        "coordinates": {"lat": 47.6028, "lng": -122.3382},
        "country": "USA"
    },
    {
        "_id": "PORT_PORTLAND",
        "name": "Port of Portland",
        "coordinates": {"lat": 45.5480, "lng": -122.7987},
        "country": "USA"
    },
    # ── Southeast Asia cluster ────────────────────────────────────────────────
    {
        "_id": "PORT_SINGAPORE",
        "name": "Port of Singapore",
        "coordinates": {"lat": 1.2640, "lng": 103.8200},
        "country": "Singapore"
    },
    {
        "_id": "PORT_PORT_KLANG",
        "name": "Port Klang",
        "coordinates": {"lat": 3.0000, "lng": 101.4000},
        "country": "Malaysia"
    },
    {
        "_id": "PORT_TANJUNG_PELEPAS",
        "name": "Port of Tanjung Pelepas",
        "coordinates": {"lat": 1.3630, "lng": 103.5500},
        "country": "Malaysia"
    },
    {
        "_id": "PORT_LAEM_CHABANG",
        "name": "Port of Laem Chabang",
        "coordinates": {"lat": 13.0818, "lng": 100.8817},
        "country": "Thailand"
    },
    {
        "_id": "PORT_JAKARTA",
        "name": "Port of Jakarta (Tanjung Priok)",
        "coordinates": {"lat": -6.1000, "lng": 106.8800},
        "country": "Indonesia"
    },
]

# ══════════════════════════════════════════════════════════════════════════════
# REGULATIONS — differentiated rules per port per cargo category
# Design intent:
#   Electronics  → blocked/penalised at San Diego (ITAR), cheap at Seattle
#   Pharma       → 5-day quarantine at LA, cold storage available at Singapore
#   Chemicals    → banned at San Francisco, allowed at Portland w/ surcharge
#   Perishables  → high tariff at Portland, fast-track at Laem Chabang
#   Luxury goods → 20% tariff at Jakarta, duty-free at Tanjung Pelepas
# ══════════════════════════════════════════════════════════════════════════════

regulations = [

    # ── Port of Los Angeles ───────────────────────────────────────────────────
    {
        "_id": "REG_LA_PHARMA",
        "port_name": "Port of Los Angeles",
        "cargo_category": "Pharmaceuticals",
        "document_reference": "LAX-REG-2024-PHARMA",
        "text": """
PORT OF LOS ANGELES — PHARMACEUTICAL IMPORT REGULATIONS
Effective Date: January 1, 2024
Document Reference: LAX-REG-2024-PHARMA

SECTION 1 — SCOPE
Governs all pharmaceutical, biologic, and medical cargo at the Port of Los Angeles.

SECTION 2 — STANDARD IMPORT PROCEDURES, Page 2
2.1 Standard customs clearance: 48 hours for non-restricted cargo.
2.2 Valid bill of lading and FDA pre-arrival notification mandatory.

SECTION 3 — TARIFF SCHEDULE, Page 4
3.1 General merchandise: 2.5% ad valorem.
3.2 Pharmaceuticals and medical devices: 4.5% ad valorem.
3.3 Biologics and vaccines: 6% ad valorem.

SECTION 4 — PHARMACEUTICAL REQUIREMENTS, Page 6
4.1 Mandatory 5-day FDA quarantine inspection for all pharmaceutical cargo regardless of origin.
4.2 Temperature-sensitive cargo must use port-certified cold-chain facilities (fee: $2,800/day).
4.3 Importer must post a compliance bond of 10% of cargo value prior to berth assignment.
4.4 All biologics require USDA-APHIS certification uploaded 72 hours before arrival.

SECTION 5 — PROHIBITED GOODS, Page 9
5.1 Unapproved compounds subject to seizure without compensation.
"""
    },
    {
        "_id": "REG_LA_ELECTRONICS",
        "port_name": "Port of Los Angeles",
        "cargo_category": "Electronics",
        "document_reference": "LAX-REG-2024-ELEC",
        "text": """
PORT OF LOS ANGELES — ELECTRONICS IMPORT REGULATIONS
Effective Date: January 1, 2024
Document Reference: LAX-REG-2024-ELEC

SECTION 1 — SCOPE
Governs consumer electronics, semiconductors, and technology hardware.

SECTION 2 — IMPORT PROCEDURES, Page 2
2.1 Customs clearance: 24–48 hours standard.
2.2 FCC compliance certification required for all wireless-enabled devices.

SECTION 3 — TARIFF SCHEDULE, Page 4
3.1 Consumer electronics: 7.5% ad valorem.
3.2 Semiconductors and components: 3.5% ad valorem.
3.3 Military-grade or dual-use technology: subject to ITAR review (30–90 day hold possible).

SECTION 4 — HANDLING, Page 6
4.1 ESD-sensitive cargo must be declared. Port provides certified ESD storage at $1,200/day.
4.2 No mandatory quarantine for standard consumer electronics.
"""
    },

    # ── Port of San Diego ─────────────────────────────────────────────────────
    {
        "_id": "REG_SD_ELECTRONICS",
        "port_name": "Port of San Diego",
        "cargo_category": "Electronics",
        "document_reference": "SAN-REG-2024-ELEC",
        "text": """
PORT OF SAN DIEGO — ELECTRONICS AND TECHNOLOGY IMPORT REGULATIONS
Effective Date: March 1, 2024
Document Reference: SAN-REG-2024-ELEC

SECTION 1 — SCOPE
Due to proximity to US Navy and defense installations, all electronics imports are subject to
enhanced ITAR screening.

SECTION 2 — ITAR SCREENING, Page 3
2.1 ALL electronics shipments — including commercial consumer goods — are subject to mandatory
ITAR pre-screening. Average processing time: 21 business days.
2.2 Dual-use components automatically escalated to DDTC review (60–120 days).
2.3 Importers must post a security deposit of 15% of declared cargo value.

SECTION 3 — TARIFF SCHEDULE, Page 5
3.1 Consumer electronics: 9% ad valorem (port security surcharge included).
3.2 Semiconductor components: 5% ad valorem.

SECTION 4 — RECOMMENDATION
4.1 High-volume commercial electronics importers are advised to use alternative California ports
to avoid mandatory ITAR delays.
"""
    },
    {
        "_id": "REG_SD_CHEMICALS",
        "port_name": "Port of San Diego",
        "cargo_category": "Chemicals",
        "document_reference": "SAN-REG-2024-CHEM",
        "text": """
PORT OF SAN DIEGO — CHEMICAL CARGO REGULATIONS
Effective Date: March 1, 2024
Document Reference: SAN-REG-2024-CHEM

SECTION 1 — SCOPE
Governs industrial chemicals, solvents, and hazardous materials.

SECTION 2 — IMPORT REQUIREMENTS, Page 2
2.1 MSDS documentation required for all chemical cargo.
2.2 Hazmat declaration mandatory 96 hours before arrival.
2.3 Chemical cargo subject to 3-day CBP inspection hold.

SECTION 3 — TARIFF SCHEDULE, Page 4
3.1 Industrial chemicals: 5% ad valorem + $850 hazmat handling fee per container.
3.2 Controlled precursor chemicals: PROHIBITED. Immediate seizure and return.
"""
    },

    # ── Port of San Francisco ─────────────────────────────────────────────────
    {
        "_id": "REG_SF_CHEMICALS",
        "port_name": "Port of San Francisco",
        "cargo_category": "Chemicals",
        "document_reference": "SFO-REG-2024-CHEM",
        "text": """
PORT OF SAN FRANCISCO — CHEMICAL AND HAZARDOUS MATERIALS REGULATIONS
Effective Date: February 1, 2024
Document Reference: SFO-REG-2024-CHEM

SECTION 1 — SCOPE
San Francisco Bay Area environmental protection zones impose the strictest chemical import
controls on the US West Coast.

SECTION 2 — PROHIBITED CARGO, Page 2
2.1 The following cargo categories are PROHIBITED at the Port of San Francisco:
    - Industrial solvents exceeding 500 metric tons per shipment
    - Chlorinated compounds (all classes)
    - Ammonia-based industrial chemicals
    - Coal tar derivatives
2.2 Violations result in vessel impoundment, $500,000 fine, and criminal referral.

SECTION 3 — ALLOWED CHEMICALS, Page 4
3.1 Food-grade and pharmaceutical-grade chemicals only, subject to EPA review.
3.2 Tariff: 8% ad valorem + mandatory $12,000 environmental impact assessment fee per vessel.

SECTION 4 — ENVIRONMENTAL SURCHARGE, Page 6
4.1 All vessels calling at San Francisco pay a $4,500 Bay Area Clean Air surcharge.
"""
    },
    {
        "_id": "REG_SF_PERISHABLES",
        "port_name": "Port of San Francisco",
        "cargo_category": "Perishables",
        "document_reference": "SFO-REG-2024-PERISH",
        "text": """
PORT OF SAN FRANCISCO — PERISHABLE GOODS IMPORT REGULATIONS
Effective Date: February 1, 2024
Document Reference: SFO-REG-2024-PERISH

SECTION 1 — SCOPE
Fresh produce, chilled meat, dairy, and seafood imports.

SECTION 2 — IMPORT REQUIREMENTS, Page 2
2.1 USDA APHIS Lacey Act declaration required for all fresh produce.
2.2 Customs clearance for perishables: priority lane, 12–18 hours.
2.3 Cold-chain certificates required.

SECTION 3 — TARIFF SCHEDULE, Page 4
3.1 Fresh produce: 3% ad valorem.
3.2 Chilled meat and dairy: 6% ad valorem + $1,800 USDA inspection fee per lot.
3.3 Seafood: 4% ad valorem.

SECTION 4 — STORAGE, Page 5
4.1 Port provides 72-hour free cold storage. Beyond 72 hours: $3,200/day.
"""
    },

    # ── Port of Seattle ───────────────────────────────────────────────────────
    {
        "_id": "REG_SEA_ELECTRONICS",
        "port_name": "Port of Seattle",
        "cargo_category": "Electronics",
        "document_reference": "SEA-REG-2024-ELEC",
        "text": """
PORT OF SEATTLE — ELECTRONICS IMPORT REGULATIONS
Effective Date: January 1, 2024
Document Reference: SEA-REG-2024-ELEC

SECTION 1 — SCOPE
Gateway for Asia-Pacific electronics destined for the Pacific Northwest and inland markets.

SECTION 2 — IMPORT PROCEDURES, Page 2
2.1 Streamlined customs clearance: 18–24 hours for pre-registered importers.
2.2 FCC certification required for wireless devices.
2.3 No ITAR screening for standard commercial consumer electronics.

SECTION 3 — TARIFF SCHEDULE, Page 4
3.1 Consumer electronics: 5.5% ad valorem. (Port incentive program active 2024.)
3.2 EV components and clean technology hardware: 2% ad valorem (green incentive).
3.3 Semiconductors: 3% ad valorem.

SECTION 4 — HANDLING, Page 5
4.1 Dedicated electronics terminal with ESD facilities included at no extra charge.
4.2 No mandatory quarantine. 
"""
    },
    {
        "_id": "REG_SEA_PHARMA",
        "port_name": "Port of Seattle",
        "cargo_category": "Pharmaceuticals",
        "document_reference": "SEA-REG-2024-PHARMA",
        "text": """
PORT OF SEATTLE — PHARMACEUTICAL IMPORT REGULATIONS
Effective Date: January 1, 2024
Document Reference: SEA-REG-2024-PHARMA

SECTION 1 — SCOPE
Pharmaceutical and medical supply imports at the Port of Seattle.

SECTION 2 — IMPORT PROCEDURES, Page 2
2.1 FDA pre-arrival notification required 48 hours before vessel arrival.
2.2 Standard customs clearance: 36 hours.

SECTION 3 — TARIFF SCHEDULE, Page 4
3.1 Pharmaceutical drugs: 3.5% ad valorem.
3.2 Medical devices: 2.5% ad valorem.

SECTION 4 — COLD CHAIN, Page 5
4.1 Port of Seattle operates a Class A cold-chain facility with uninterrupted temperature
monitoring at no additional holding fee for first 5 days.
4.2 No mandatory quarantine for pre-cleared pharmaceutical cargo from approved origins.
4.3 Biologics require USDA-APHIS certification only.

SECTION 5 — RECOMMENDATION
5.1 Temperature-sensitive pharmaceutical importers are strongly encouraged to use Seattle
over LA or San Francisco for significantly lower handling costs and zero quarantine delays.
"""
    },

    # ── Port of Portland ──────────────────────────────────────────────────────
    {
        "_id": "REG_PORT_CHEMICALS",
        "port_name": "Port of Portland",
        "cargo_category": "Chemicals",
        "document_reference": "PDX-REG-2024-CHEM",
        "text": """
PORT OF PORTLAND — CHEMICAL CARGO IMPORT REGULATIONS
Effective Date: April 1, 2024
Document Reference: PDX-REG-2024-CHEM

SECTION 1 — SCOPE
Industrial chemicals, agricultural chemicals, and processing solvents.

SECTION 2 — IMPORT REQUIREMENTS, Page 2
2.1 MSDS documentation required. Oregon DEQ notification required 72 hours before arrival.
2.2 Customs hold: 48 hours for all chemical cargo.
2.3 No prohibition on chlorinated compounds below 1,000 MT per shipment.

SECTION 3 — TARIFF SCHEDULE, Page 4
3.1 Industrial chemicals: 4% ad valorem + $600 DEQ inspection fee per container.
3.2 Agricultural chemicals and fertilizers: 3.5% ad valorem.
3.3 Hazardous materials surcharge: $1,200 per vessel.

SECTION 4 — STORAGE, Page 5
4.1 Dedicated chemical terminal with secondary containment.
4.2 Maximum dwell time: 30 days before mandatory removal.
"""
    },
    {
        "_id": "REG_PORT_PERISHABLES",
        "port_name": "Port of Portland",
        "cargo_category": "Perishables",
        "document_reference": "PDX-REG-2024-PERISH",
        "text": """
PORT OF PORTLAND — PERISHABLE GOODS REGULATIONS
Effective Date: April 1, 2024
Document Reference: PDX-REG-2024-PERISH

SECTION 1 — SCOPE
Fresh produce, chilled meat, and temperature-sensitive food cargo.

SECTION 2 — TARIFF SCHEDULE, Page 2
2.1 Fresh produce: 5.5% ad valorem. (Higher than regional average due to limited terminal capacity.)
2.2 Meat and dairy: 8% ad valorem + $2,400 USDA inspection fee.
2.3 Seafood: 5% ad valorem.

SECTION 3 — COLD STORAGE, Page 3
3.1 Limited cold storage availability. Pre-booking mandatory 14 days in advance.
3.2 Cold storage fee: $4,100/day beyond 48-hour free window.

SECTION 4 — RECOMMENDATION
4.1 High-volume perishable shippers are advised to consider Seattle or San Francisco
for better terminal capacity and lower per-unit handling costs.
"""
    },

    # ── Port of Singapore ─────────────────────────────────────────────────────
    {
        "_id": "REG_SG_PHARMA",
        "port_name": "Port of Singapore",
        "cargo_category": "Pharmaceuticals",
        "document_reference": "SG-REG-2024-PHARMA",
        "text": """
PORT OF SINGAPORE — PHARMACEUTICAL IMPORT REGULATIONS
Effective Date: January 1, 2024
Document Reference: SG-REG-2024-PHARMA

SECTION 1 — SCOPE
Pharmaceutical and biomedical cargo at one of Asia's primary pharmaceutical logistics hubs.

SECTION 2 — IMPORT PROCEDURES, Page 2
2.1 HSA (Health Sciences Authority) import licence required.
2.2 Clearance time: 24 hours for pre-registered importers.
2.3 No mandatory quarantine for goods from GMP-certified facilities.

SECTION 3 — TARIFF SCHEDULE, Page 4
3.1 Singapore operates a zero-tariff policy on pharmaceutical imports (0% ad valorem).
3.2 Port handling fee: SGD 850 per container (approx. USD 630).

SECTION 4 — COLD CHAIN, Page 5
4.1 World-class GDP-compliant cold-chain facilities available 24/7.
4.2 Temperature monitoring with blockchain audit trail.
4.3 No additional cold-chain fee for first 7 days.

SECTION 5 — STRATEGIC ADVANTAGE, Page 6
5.1 Singapore serves as ASEAN pharmaceutical redistribution hub.
5.2 Re-export allowed with MAS financial incentives for regional distributors.
"""
    },
    {
        "_id": "REG_SG_LUXURY",
        "port_name": "Port of Singapore",
        "cargo_category": "Luxury Goods",
        "document_reference": "SG-REG-2024-LUX",
        "text": """
PORT OF SINGAPORE — LUXURY GOODS IMPORT REGULATIONS
Effective Date: January 1, 2024
Document Reference: SG-REG-2024-LUX

SECTION 1 — SCOPE
Luxury watches, jewellery, designer apparel, fine spirits, and premium consumer goods.

SECTION 2 — IMPORT PROCEDURES, Page 2
2.1 Singapore Customs: GST 9% on luxury goods above SGD 400 per item.
2.2 Clearance: 24 hours.

SECTION 3 — TARIFF SCHEDULE, Page 4
3.1 Luxury goods: 9% GST (no additional ad valorem tariff).
3.2 Fine wines and spirits: 9% GST + SGD 88/litre excise duty.

SECTION 4 — FREEPORT OPTION, Page 5
4.1 Singapore Freeport storage available for high-value goods with full duty deferral.
4.2 Freeport fee: 0.25% of cargo value per annum.
"""
    },

    # ── Port Klang ────────────────────────────────────────────────────────────
    {
        "_id": "REG_KLANG_ELECTRONICS",
        "port_name": "Port Klang",
        "cargo_category": "Electronics",
        "document_reference": "PKL-REG-2024-ELEC",
        "text": """
PORT KLANG — ELECTRONICS IMPORT REGULATIONS
Effective Date: January 1, 2024
Document Reference: PKL-REG-2024-ELEC

SECTION 1 — SCOPE
Electronics and technology cargo at Malaysia's largest port.

SECTION 2 — IMPORT PROCEDURES, Page 2
2.1 SIRIM certification required for wireless and electrical devices (Malaysian standard).
2.2 Customs clearance: 24–36 hours.

SECTION 3 — TARIFF SCHEDULE, Page 4
3.1 Consumer electronics: 0% import duty (Malaysia AFTA preference).
3.2 Smartphones and computers: 0% import duty.
3.3 Luxury electronics (above MYR 5,000/unit): 10% sales tax.

SECTION 4 — FREE ZONE OPTION, Page 5
4.1 Westport Free Zone: full import duty exemption for re-export.
4.2 Free Zone handling fee: USD 180 per container.
"""
    },
    {
        "_id": "REG_KLANG_CHEMICALS",
        "port_name": "Port Klang",
        "cargo_category": "Chemicals",
        "document_reference": "PKL-REG-2024-CHEM",
        "text": """
PORT KLANG — CHEMICAL CARGO REGULATIONS
Effective Date: January 1, 2024
Document Reference: PKL-REG-2024-CHEM

SECTION 1 — SCOPE
Industrial and specialty chemical imports at Port Klang.

SECTION 2 — IMPORT REQUIREMENTS, Page 2
2.1 DOE (Department of Environment Malaysia) hazardous goods permit required.
2.2 Scheduled waste regulations apply to chemical residues.
2.3 Customs hold: 72 hours for Class 3–8 hazardous chemicals.

SECTION 3 — TARIFF SCHEDULE, Page 4
3.1 Industrial chemicals: 5% import duty.
3.2 Petrochemicals: 0% (AFTA exemption).
3.3 Hazmat handling surcharge: USD 420 per container.
"""
    },

    # ── Port of Tanjung Pelepas ───────────────────────────────────────────────
    {
        "_id": "REG_TPP_LUXURY",
        "port_name": "Port of Tanjung Pelepas",
        "cargo_category": "Luxury Goods",
        "document_reference": "TPP-REG-2024-LUX",
        "text": """
PORT OF TANJUNG PELEPAS — LUXURY GOODS IMPORT REGULATIONS
Effective Date: January 1, 2024
Document Reference: TPP-REG-2024-LUX

SECTION 1 — SCOPE
Luxury and high-value consumer goods transiting or importing through Tanjung Pelepas FTZ.

SECTION 2 — FREE TRADE ZONE ADVANTAGES, Page 2
2.1 Tanjung Pelepas operates a full Free Trade Zone with 0% import duty on luxury goods
for onward distribution to ASEAN markets.
2.2 No GST applicable within FTZ boundary.
2.3 Clearance time: 12–18 hours for pre-declared cargo.

SECTION 3 — TARIFF SCHEDULE, Page 3
3.1 Luxury goods within FTZ: 0% import duty, 0% GST.
3.2 Goods exiting FTZ to Malaysian domestic market: 10% sales tax applies.

SECTION 4 — HANDLING, Page 4
4.1 High-value cargo vault storage available, USD 0.08 per USD 1,000 cargo value per day.
4.2 Armed escort from vessel to bonded warehouse included.
"""
    },
    {
        "_id": "REG_TPP_ELECTRONICS",
        "port_name": "Port of Tanjung Pelepas",
        "cargo_category": "Electronics",
        "document_reference": "TPP-REG-2024-ELEC",
        "text": """
PORT OF TANJUNG PELEPAS — ELECTRONICS IMPORT REGULATIONS
Effective Date: January 1, 2024
Document Reference: TPP-REG-2024-ELEC

SECTION 1 — SCOPE
Consumer and industrial electronics transshipment hub.

SECTION 2 — IMPORT PROCEDURES, Page 2
2.1 Free Trade Zone: 0% import duty for transshipment cargo.
2.2 SIRIM certification not required within FTZ (required only on exit to Malaysian market).
2.3 Customs clearance: 12 hours.

SECTION 3 — TARIFF SCHEDULE, Page 3
3.1 Electronics for re-export: 0% tariff.
3.2 Electronics entering domestic market: 0% duty + 8% sales tax.
"""
    },

    # ── Port of Laem Chabang ──────────────────────────────────────────────────
    {
        "_id": "REG_LC_PERISHABLES",
        "port_name": "Port of Laem Chabang",
        "cargo_category": "Perishables",
        "document_reference": "LCB-REG-2024-PERISH",
        "text": """
PORT OF LAEM CHABANG — PERISHABLE GOODS IMPORT REGULATIONS
Effective Date: January 1, 2024
Document Reference: LCB-REG-2024-PERISH

SECTION 1 — SCOPE
Fresh produce, frozen food, and temperature-controlled cargo at Thailand's primary deep-water port.

SECTION 2 — IMPORT PROCEDURES, Page 2
2.1 Thai FDA import permit required for food products.
2.2 Priority perishables lane: clearance in 8–12 hours (fastest in Southeast Asia).
2.3 HACCP compliance certificate required.

SECTION 3 — TARIFF SCHEDULE, Page 4
3.1 Fresh fruit and vegetables: 30% ad valorem (standard Thai tariff) — ASEAN origin: 0%.
3.2 Frozen meat: 30% ad valorem — ASEAN origin: 5%.
3.3 Seafood: 5% ad valorem (all origins).

SECTION 4 — COLD STORAGE, Page 5
4.1 B.Grimm cold chain facility: state-of-the-art -25°C to +15°C range.
4.2 Free cold storage: first 48 hours. After: THB 3,500/container/day (approx. USD 100).
4.3 Reefer plug capacity: 4,200 TEU simultaneous.

SECTION 5 — STRATEGIC NOTE, Page 6
5.1 Laem Chabang is the recommended entry for perishables destined for ASEAN markets
due to the lowest per-day cold storage cost in the region.
"""
    },
    {
        "_id": "REG_LC_CHEMICALS",
        "port_name": "Port of Laem Chabang",
        "cargo_category": "Chemicals",
        "document_reference": "LCB-REG-2024-CHEM",
        "text": """
PORT OF LAEM CHABANG — CHEMICAL IMPORT REGULATIONS
Effective Date: January 1, 2024
Document Reference: LCB-REG-2024-CHEM

SECTION 1 — SCOPE
Industrial chemicals, petrochemicals, and specialty chemicals.

SECTION 2 — IMPORT REQUIREMENTS, Page 2
2.1 Thai Industrial Estate Authority permit for hazardous chemicals.
2.2 IMO declaration mandatory for IMDG Class 3–8 cargo.
2.3 Customs hold: 48 hours.

SECTION 3 — TARIFF SCHEDULE, Page 4
3.1 Industrial chemicals: 3% ad valorem (one of the lowest in Southeast Asia).
3.2 Petrochemicals: 1% ad valorem.
3.3 Specialty chemicals: 5% ad valorem.
"""
    },

    # ── Port of Jakarta ───────────────────────────────────────────────────────
    {
        "_id": "REG_JKT_LUXURY",
        "port_name": "Port of Jakarta (Tanjung Priok)",
        "cargo_category": "Luxury Goods",
        "document_reference": "JKT-REG-2024-LUX",
        "text": """
PORT OF JAKARTA — LUXURY GOODS IMPORT REGULATIONS
Effective Date: January 1, 2024
Document Reference: JKT-REG-2024-LUX

SECTION 1 — SCOPE
High-value consumer goods, luxury items, and premium branded products.

SECTION 2 — IMPORT REQUIREMENTS, Page 2
2.1 Ministry of Trade import licence (API-U) mandatory.
2.2 Customs clearance: 5–7 business days (manual verification standard).
2.3 Physical inspection of 30% of luxury cargo containers is mandatory.

SECTION 3 — TARIFF SCHEDULE, Page 4
3.1 Luxury goods: 20% import duty + 11% VAT + 10% luxury goods sales tax (PPnBM).
3.2 Effective total tax burden on luxury goods: approximately 45–50% of CIF value.
3.3 Luxury watches and jewellery above USD 5,000/unit: additional 25% PPnBM.

SECTION 4 — PROHIBITED ITEMS, Page 6
4.1 Certain branded luxury goods exceeding government-set price thresholds are subject to
import quota restrictions. Importers must obtain a quota allocation in advance.
"""
    },
    {
        "_id": "REG_JKT_PERISHABLES",
        "port_name": "Port of Jakarta (Tanjung Priok)",
        "cargo_category": "Perishables",
        "document_reference": "JKT-REG-2024-PERISH",
        "text": """
PORT OF JAKARTA — PERISHABLE GOODS IMPORT REGULATIONS
Effective Date: January 1, 2024
Document Reference: JKT-REG-2024-PERISH

SECTION 1 — SCOPE
Fresh and frozen food imports at Indonesia's busiest port.

SECTION 2 — IMPORT REQUIREMENTS, Page 2
2.1 BPOM (Indonesian FDA) import approval required — processing time: 14–21 days.
2.2 Halal certification mandatory for all meat and processed food products.
2.3 Physical inspection: 100% of perishable cargo subject to sampling.
2.4 Average customs clearance: 5–8 business days.

SECTION 3 — TARIFF SCHEDULE, Page 4
3.1 Fresh fruit: 5% import duty.
3.2 Frozen meat: 5% import duty (quota-controlled; non-quota: 30%).
3.3 Dairy products: 5% import duty + mandatory BPOM lab testing fee (USD 1,800/lot).

SECTION 4 — COLD STORAGE, Page 5
4.1 Cold storage limited. Average wait time for reefer plug: 18–36 hours upon arrival.
4.2 Fee: USD 220/container/day after 24-hour free window.

SECTION 5 — RISK NOTE, Page 6
5.1 Port of Jakarta is NOT recommended for time-critical perishable cargo due to
lengthy BPOM approval timelines and limited cold storage capacity.
"""
    },
]

# ══════════════════════════════════════════════════════════════════════════════
# VESSELS — 20 vessels, varied cargo, split across the two port clusters
# Original port = Port of Long Beach (US cluster origin for disruption demo)
# or Port of Singapore (SEA cluster origin for disruption demo)
# ══════════════════════════════════════════════════════════════════════════════

vessels = [
    # ── Pharmaceuticals (temp-sensitive) — routed to US ports ─────────────────
    {
        "_id": "MV_HELIOS_002",
        "vessel_name": "MV Helios",
        "coordinates": {"lat": 34.012, "lng": -119.412},
        "cargo": {
            "type": "Pharmaceuticals",
            "temperature_sensitive": True,
            "containers": 310,
            "value_usd": 18500000
        },
        "original_port": "Port of Long Beach",
        "status": "DISRUPTED"
    },
    {
        "_id": "MV_CORONA_003",
        "vessel_name": "MV Corona Star",
        "coordinates": {"lat": 33.501, "lng": -118.852},
        "cargo": {
            "type": "Pharmaceuticals",
            "temperature_sensitive": True,
            "containers": 180,
            "value_usd": 9200000
        },
        "original_port": "Port of Long Beach",
        "status": "DISRUPTED"
    },
    # ── Electronics — routed to US ports ─────────────────────────────────────
    {
        "_id": "MV_TITAN_004",
        "vessel_name": "MV Titan",
        "coordinates": {"lat": 33.902, "lng": -119.100},
        "cargo": {
            "type": "Electronics",
            "temperature_sensitive": False,
            "containers": 620,
            "value_usd": 34000000
        },
        "original_port": "Port of Long Beach",
        "status": "DISRUPTED"
    },
    {
        "_id": "MV_NOVA_005",
        "vessel_name": "MV Nova",
        "coordinates": {"lat": 33.650, "lng": -118.500},
        "cargo": {
            "type": "Electronics",
            "temperature_sensitive": False,
            "containers": 400,
            "value_usd": 22000000
        },
        "original_port": "Port of Long Beach",
        "status": "DISRUPTED"
    },
    # ── Chemicals — routed to US ports ────────────────────────────────────────
    {
        "_id": "MV_CHEM_EAGLE_006",
        "vessel_name": "MV Chem Eagle",
        "coordinates": {"lat": 33.400, "lng": -118.100},
        "cargo": {
            "type": "Chemicals",
            "temperature_sensitive": False,
            "containers": 290,
            "value_usd": 7800000
        },
        "original_port": "Port of Long Beach",
        "status": "DISRUPTED"
    },
    {
        "_id": "MV_SOLVENT_007",
        "vessel_name": "MV Solvent Prince",
        "coordinates": {"lat": 33.800, "lng": -118.600},
        "cargo": {
            "type": "Chemicals",
            "temperature_sensitive": False,
            "containers": 350,
            "value_usd": 11200000
        },
        "original_port": "Port of Long Beach",
        "status": "DISRUPTED"
    },
    # ── Perishables — routed to US ports ──────────────────────────────────────
    {
        "_id": "MV_FRESH_WIND_008",
        "vessel_name": "MV Fresh Wind",
        "coordinates": {"lat": 33.300, "lng": -118.300},
        "cargo": {
            "type": "Perishables",
            "temperature_sensitive": True,
            "containers": 210,
            "value_usd": 5400000
        },
        "original_port": "Port of Long Beach",
        "status": "DISRUPTED"
    },
    {
        "_id": "MV_HARVEST_009",
        "vessel_name": "MV Harvest Moon",
        "coordinates": {"lat": 33.950, "lng": -118.750},
        "cargo": {
            "type": "Perishables",
            "temperature_sensitive": True,
            "containers": 175,
            "value_usd": 3900000
        },
        "original_port": "Port of Long Beach",
        "status": "DISRUPTED"
    },
    # ── Luxury Goods — routed to US ports ────────────────────────────────────
    {
        "_id": "MV_PRESTIGE_010",
        "vessel_name": "MV Prestige",
        "coordinates": {"lat": 33.600, "lng": -119.000},
        "cargo": {
            "type": "Luxury Goods",
            "temperature_sensitive": False,
            "containers": 95,
            "value_usd": 48000000
        },
        "original_port": "Port of Long Beach",
        "status": "DISRUPTED"
    },
    {
        "_id": "MV_OPULENT_011",
        "vessel_name": "MV Opulent",
        "coordinates": {"lat": 33.750, "lng": -118.900},
        "cargo": {
            "type": "Luxury Goods",
            "temperature_sensitive": False,
            "containers": 80,
            "value_usd": 62000000
        },
        "original_port": "Port of Long Beach",
        "status": "DISRUPTED"
    },
    # ── SEA cluster — original port: Port of Singapore ────────────────────────
    {
        "_id": "MV_ORIENT_012",
        "vessel_name": "MV Orient Star",
        "coordinates": {"lat": 1.150, "lng": 104.100},
        "cargo": {
            "type": "Electronics",
            "temperature_sensitive": False,
            "containers": 540,
            "value_usd": 29000000
        },
        "original_port": "Port of Singapore",
        "status": "DISRUPTED"
    },
    {
        "_id": "MV_PACIFIC_JADE_013",
        "vessel_name": "MV Pacific Jade",
        "coordinates": {"lat": 1.400, "lng": 103.700},
        "cargo": {
            "type": "Pharmaceuticals",
            "temperature_sensitive": True,
            "containers": 260,
            "value_usd": 14500000
        },
        "original_port": "Port of Singapore",
        "status": "DISRUPTED"
    },
    {
        "_id": "MV_AMBER_014",
        "vessel_name": "MV Amber",
        "coordinates": {"lat": 1.050, "lng": 103.950},
        "cargo": {
            "type": "Luxury Goods",
            "temperature_sensitive": False,
            "containers": 110,
            "value_usd": 55000000
        },
        "original_port": "Port of Singapore",
        "status": "DISRUPTED"
    },
    {
        "_id": "MV_TROPIC_015",
        "vessel_name": "MV Tropic Sun",
        "coordinates": {"lat": 1.300, "lng": 104.200},
        "cargo": {
            "type": "Perishables",
            "temperature_sensitive": True,
            "containers": 320,
            "value_usd": 6800000
        },
        "original_port": "Port of Singapore",
        "status": "DISRUPTED"
    },
    {
        "_id": "MV_REEF_016",
        "vessel_name": "MV Reef Runner",
        "coordinates": {"lat": 1.180, "lng": 103.600},
        "cargo": {
            "type": "Chemicals",
            "temperature_sensitive": False,
            "containers": 410,
            "value_usd": 9600000
        },
        "original_port": "Port of Singapore",
        "status": "DISRUPTED"
    },
    {
        "_id": "MV_COMPASS_017",
        "vessel_name": "MV Compass Rose",
        "coordinates": {"lat": 1.350, "lng": 103.500},
        "cargo": {
            "type": "Electronics",
            "temperature_sensitive": False,
            "containers": 480,
            "value_usd": 26500000
        },
        "original_port": "Port of Singapore",
        "status": "DISRUPTED"
    },
    {
        "_id": "MV_MONSOON_018",
        "vessel_name": "MV Monsoon",
        "coordinates": {"lat": 1.500, "lng": 104.000},
        "cargo": {
            "type": "Perishables",
            "temperature_sensitive": True,
            "containers": 190,
            "value_usd": 4200000
        },
        "original_port": "Port of Singapore",
        "status": "DISRUPTED"
    },
    {
        "_id": "MV_JADE_DRAGON_019",
        "vessel_name": "MV Jade Dragon",
        "coordinates": {"lat": 1.100, "lng": 103.800},
        "cargo": {
            "type": "Luxury Goods",
            "temperature_sensitive": False,
            "containers": 70,
            "value_usd": 71000000
        },
        "original_port": "Port of Singapore",
        "status": "DISRUPTED"
    },
    {
        "_id": "MV_DELTA_WAVE_020",
        "vessel_name": "MV Delta Wave",
        "coordinates": {"lat": 1.250, "lng": 104.300},
        "cargo": {
            "type": "Chemicals",
            "temperature_sensitive": False,
            "containers": 370,
            "value_usd": 8300000
        },
        "original_port": "Port of Singapore",
        "status": "DISRUPTED"
    },
    {
        "_id": "MV_STERLING_021",
        "vessel_name": "MV Sterling",
        "coordinates": {"lat": 1.420, "lng": 103.850},
        "cargo": {
            "type": "Pharmaceuticals",
            "temperature_sensitive": True,
            "containers": 290,
            "value_usd": 16800000
        },
        "original_port": "Port of Singapore",
        "status": "DISRUPTED"
    },
]

# ══════════════════════════════════════════════════════════════════════════════
# INSERT
# ══════════════════════════════════════════════════════════════════════════════

print("\n── Seeding demo data ────────────────────────────────────")
safe_insert(db.ports,       ports)
safe_insert(db.regulations, regulations)
safe_insert(db.vessels,     vessels)
print("── Done ─────────────────────────────────────────────────\n")

client.close()