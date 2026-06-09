import os
from dotenv import load_dotenv

load_dotenv()

GCP_PROJECT   = os.environ["GOOGLE_CLOUD_PROJECT"]
GCP_REGION    = os.getenv("GOOGLE_CLOUD_REGION", "us-central1")

MONGO_URI     = os.environ["MONGO_URI"]
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "LogisticsDB")

# SWITCHED TO FLASH FOR 4x FASTER EXECUTION IN THE DEMO
GEMINI_MODEL  = "gemini-2.5-flash"

RULES_SERVER_URL = os.getenv("ROOT_RULES_SERVER_URL", "")
RULES_SERVER_PORT = int(os.getenv("RULES_SERVER_PORT", "8080"))