# 🚢 CORE: Compliance-Optimized Rerouting Engine

> **Google Cloud Rapid Agent Hackathon Submission**
> Powered by **Gemini 2.5 Flash · Google ADK · MongoDB Atlas**

---

## The Problem

When a major port closes (labor strike, weather, geopolitical crisis), a human
Supply Chain Manager must manually cross-reference satellite tracking, cargo
manifests, and hundreds of pages of international customs law to find a legal
alternative destination. This takes **days**. During those days, the shipping
company bleeds millions in demurrage fees.

## The Solution

A multi-level autonomous agent system that finds legally compliant, cargo-safe,
cost-optimized rerouting solutions in **seconds** — running parallel optimizations
for an entire fleet and presenting the final manifests to a human for approval.

---

## Architecture

```
┌────────────────────────────────────────────────────────┐
│              TRIGGER AGENT (Level 1)                   │
│              Fleet Disruption Coordinator              │
│              (Handles UI dispatch & fleet scope)       │
└──────────────────────┬─────────────────────────────────┘
                       │ Spawns parallel sub-sessions
┌──────────────────────▼─────────────────────────────────┐
│              ROOT AGENT (Level 2)                      │
│              Autonomous Logistics Orchestrator         │
│              Gemini 2.5 Flash via Vertex AI            │
│                                                        │
│  ┌────────────────────────────────────────────────┐    │
│  │  MongoDB MCP Server  (@modelcontextprotocol/   │    │
│  │  server-mongodb via npx)                       │    │
│  │                                                │    │
│  │  READ  → vessels, ports, regulations (find)    │    │
│  │  WRITE → rerouting_logs (insert-many)          │    │
│  └────────────────────────────────────────────────┘    │
│                                                        │
│  ┌──────────────────────────────────────────────────┐  │
│  │  Custom Rules MCP Server (FastMCP rules_server)  │  │
│  │  calculate_route_cost                            │  │
│  │  find_closest_alternative_ports                  │  │
│  │  build_rerouting_manifest                        │  │
│  │  build_escalation_record                         │  │
│  └──────────────────────────────────────────────────┘  │
│                                                        │
│  ┌──────────────────────────────────────────────────┐  │
│  │  COMPLIANCE SUB-AGENT  (ADK FunctionTool)        │  │
│  │  Gemini 2.5 Flash                                │  │
│  │  Reads raw regulatory text, returns APPROVED/    │  │
│  │  REJECTED with exact legal citation              │  │
│  └──────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────┘
                       │
          ┌────────────▼───────────────┐
          │  Streamlit UI (Cloud Run)  │
          │  Live telemetry · Manifest │
          │  Human-in-the-loop Chat    │
          └────────────────────────────┘
```

### Why this is a real agent (not a pipeline)

The Root Agent's system prompt gives it **values and constraints**, not a
workflow sequence. Gemini decides what tools to call, in what order, and when
to stop — via ADK's native ReAct loop. The Compliance Sub-Agent is a fully
separate Gemini instance (wrapped as an `AgentTool`) — the Root Agent calls
it as a black-box tool and receives a verdict without seeing its internal
reasoning steps.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Agent Framework | Google ADK (`google-adk`) |
| LLM | Gemini 2.5 Flash via Vertex AI |
| Database | MongoDB Atlas |
| MongoDB MCP | `@modelcontextprotocol/server-mongodb` (npx stdio) |
| Custom MCP | FastMCP (`rules_server.py`) on Cloud Run |
| UI | Streamlit on Cloud Run |
| Deployment | Google Cloud Build + Cloud Run |

### MongoDB MCP Integration

The Root Agent connects to MongoDB Atlas through the **official MongoDB MCP server**
(`@modelcontextprotocol/server-mongodb`), wired in as an `MCPToolset` via ADK.

The agent uses the MCP `find` tool for all **read** operations:

```json
{ "database": "LogisticsDB", "collection": "vessels",     "filter": {"_id": "MV_ATLAS_001"} }
{ "database": "LogisticsDB", "collection": "ports",       "filter": {} }
{ "database": "LogisticsDB", "collection": "regulations", "filter": {"port_name": "Port of Seattle"} }
```

And the MCP `insert-many` tool to **write** the final rerouting manifest or the escalation:

```json
{
  "database":   "LogisticsDB",
  "collection": "rerouting_logs",
  "documents":  [{ "...manifest..." }]
}
```

---

## Demo Scenario

1. **Port of Singapore** closes due to an explosion.
2. Vessel **MV Sterling** is stranded carrying **temperature-sensitive pharmaceuticals** worth $16M.
3. The trigger agent dispatches the Root Agent to evaluate alternative ports:
   - **Port of Tanjung Pelepas** → ❌ REJECTED — No regulations found for Pharmaceuticals, compliance unverified.
   - **Port of Jakarta** → ❌ REJECTED — Regulations only cover luxury goods, making pharma entry impossible.
4. Final manifest resolves an approved route if available, or escalates if all fail.
   The user can chat with the Root Agent natively in the UI to ask why certain decisions were made.

*This would have taken hours to days from a human.*

---

## Local Setup

### Prerequisites

- Python 3.11+
- Docker (for MongoDB MCP server)
- Google Cloud project with Vertex AI API enabled
- MongoDB Atlas cluster

### 1. Clone and install

```bash
git clone https://github.com/YOUR_GITHUB_USERNAME/autonomous-routing
cd autonomous-routing
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your real values
```

### 3. Seed MongoDB

```bash
python data/seed_demo_db.py
python data/seed_mongodb.py
```

### 4. Option 1: Test the agent from terminal

```bash
python runner.py
```

### 5. Option 2: Run the Streamlit UI

```bash
streamlit run ui/app.py
```

---

## Cloud Deployment

### Build and deploy with Cloud Build

```bash
gcloud builds submit --config deploy/cloudbuild.yaml .
```

This builds and deploys two Cloud Run services:

- `rules-server` — the custom Rules MCP server
- `routing-ui` — the Streamlit dashboard

After deployment, set `ROOT_RULES_SERVER_URL` on the `routing-ui` service
to point to the `rules-server` Cloud Run URL.

---

## Project Structure

```
autonomous-routing/
├── agents/
│   ├── trigger_agent.py      # ADK LlmAgent — Level 1 Dispatcher
│   ├── root_agent.py         # ADK LlmAgent — Level 2 Orchestrator
│   └── compliance_agent.py   # ADK LlmAgent — Compliance specialist
├── mcp_servers/
│   └── rules_server.py       # FastMCP — Deterministic business logic
├── toolsets/
│   └── connections.py        # MCPToolset factory functions
├── data/
│   ├── seed_demo_db.py       # Demo DB generator
│   └── seed_mongodb.py       # Regulatory/Core DB seeder
├── ui/
│   └── app.py                # Streamlit UI Dashboard
├── deploy/
│   ├── Dockerfile.rules      # Rules server container
│   ├── Dockerfile.ui         # Streamlit container
│   └── cloudbuild.yaml       # GCP build pipeline
├── runner.py                 # ADK Runner — terminal entry point
├── session_service.py        # In-memory agent state manager
├── config.py                 # Environment config
├── .env.example              # Env template
└── requirements.txt
```

---

## Team

**Omar Nesredin** — AI/Cloud Engineering & Backend Architecture

---

## License

MIT
