"""
migrate_vessel_ids.py
---------------------
Convert all vessel _id fields from ObjectId to strings in MongoDB.
Run this to fix the type mismatch.
"""

import os
from pymongo import MongoClient
from bson import ObjectId

# MongoDB Atlas
MONGO_URI="mongodb+srv://3mar:reem2006@cluster0.fu7slfw.mongodb.net/?appName=Cluster0"
MONGO_DB_NAME="LogisticsDB"

def migrate_vessel_ids():
    """Convert vessel _id from ObjectId to string."""
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=4000)
        db = client[MONGO_DB_NAME]
        
        # Find all vessels with ObjectId _id
        vessels = list(db.vessels.find({}))
        
        print(f"\nFound {len(vessels)} vessel(s)")
        
        migrated = 0
        for v in vessels:
            vid = v["_id"]
            print(f"\n  Current _id: {vid} (type: {type(vid).__name__})")
            
            # If it's already a string, skip
            if isinstance(vid, str):
                print(f"    ✓ Already a string, skipping")
                continue
            
            # If it's an ObjectId, convert and migrate
            if isinstance(vid, ObjectId):
                new_vid = str(vid)
                print(f"    Converting to: {new_vid}")
                
                # Remove old document
                db.vessels.delete_one({"_id": vid})
                
                # Re-insert with string ID
                v["_id"] = new_vid
                db.vessels.insert_one(v)
                
                print(f"    ✓ Migrated")
                migrated += 1
        
        # Also update any references in other collections
        print(f"\n  Updating references in other collections...")
        
        # Update disruptions.affected_vessels
        disruptions = list(db.disruptions.find({}))
        for d in disruptions:
            vessel_ids = d.get("affected_vessels", [])
            updated = []
            for vid in vessel_ids:
                if isinstance(vid, ObjectId):
                    updated.append(str(vid))
                else:
                    updated.append(vid)
            if updated != vessel_ids:
                db.disruptions.update_one(
                    {"_id": d["_id"]},
                    {"$set": {"affected_vessels": updated}}
                )
                print(f"    ✓ Updated disruption {d['_id']}")
        
        client.close()
        print(f"\n✅ Migration complete! Converted {migrated} vessel(s)")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    print("MongoDB Vessel ID Migration")
    print("============================")
    print(f"URI: {MONGO_URI}")
    print(f"DB:  {MONGO_DB_NAME}")
    migrate_vessel_ids()
