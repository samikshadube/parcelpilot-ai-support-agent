# ParcelPilot AI Support & Operations Agent

**ParcelPilot** is a production-grade AI agent system for a multi-carrier B2B logistics platform featuring a dual-persona architecture:
1. **Customer-Facing Support Agent**: Assisting business accounts with order lookups, cancellation terms, SLA service credit claims, and ticket tracking under strict per-account data isolation.
2. **Internal Support & Operations Agent**: Equipping operations and engineering leads to audit SLA compliance, resolve complex contractual vs. standard policy conflicts, investigate root causes (e.g. carrier webhook sync delays, security exposures), and safely stage two-phase operational actions.

The system combines a deterministic **5-tier Source Authority Engine**, tool-level access enforcement via injected `UserContext`, zero wall-clock dependency (all temporal math evaluates against a fixed reference dataset snapshot), and a multi-provider fallback chain (**Groq → NVIDIA NIM → Deterministic Offline Engine**).

---

## 🌟 Key Capabilities & Architectural Highlights

- **5-Tier Source Authority Model (Truth & Conflict Resolution)**:
  - **Tier 1 (Governing Contracts)**: Account-specific customer agreements (e.g., Northstar cancellation fee waivers, LumenWorks fixed INR 300 delay credit overrides).
  - **Tier 2 (Current Standard Policy)**: Policy & SOP v4 covering standard INR 250 cancellation fees and 2.0-hour delay credit calculations.
  - **Tier 3 (Operational Reality)**: Product Operations Guide and Known Issue registry (`KI-208`, `KI-211`).
  - **Tier 4 (Deprecated Baseline)**: Deprecated Policy v2, explicitly barred from overriding current rules.
  - **Tier 5 (Historical Context)**: Past closed tickets, cited purely as unverified operational context.
- **Strict Data-Layer Access Control**:
  - `UserContext` is injected into every tool invocation by the agent harness.
  - Scoping is enforced directly in SQL queries (`WHERE account_id = :ctx.account_id`). Unauthorized cross-account access attempts return an immediate `AccessDenied` exception with zero data or metadata leakage.
- **Two-Phase Action Safety ("Confirm Before Acting")**:
  - State-changing operations (such as ticket status escalations or refund staging) are bifurcated into `propose_action` (generates a staged proposal requiring user confirmation) and `execute_action` (executes only after explicit user approval, re-validating permissions).
- **Zero Wall-Clock Dependency**:
  - All temporal logic (SLA breach checks, elapsed time, pickup delay windows) is evaluated deterministically against the dataset snapshot timestamp (`2026-08-16 11:00 Asia/Kolkata`) stored in the SQLite database `meta` table.
- **Multi-Provider LLM Fallback Chain with Offline Mode**:
  - Primary LLM: **Groq** (`openai/gpt-oss-120b`).
  - Secondary LLM: **NVIDIA NIM** (`nvidia/nemotron-3-nano-30b-a3b`).
  - Offline Fallback: **Deterministic Multi-Step Reasoning Engine** ensuring 100% test and evaluation coverage without any external API connectivity.
- **Full Traceability Streamlit UI**:
  - Expandable **Tool Execution Traces** (tool name, exact JSON input arguments, latency, and full structured output).
  - **Token Telemetry Cards** displaying real-time prompt/completion tokens, model details, and remaining rate limits.
  - **Source Authority Badges** with interactive citation previews.
  - Internal Ops view includes real-time **Proactive SLA Breach Detection** & **Known Issue Correlation**.

---

## 📂 Repository Structure

```
.
├── ARCHITECTURE.md                  # Comprehensive architectural specification & design choices
├── PRODUCT_NOTE.md                  # Product strategy, rollout metrics, and customer impact
├── README.md                        # Setup, API configuration, reproduction, and test guide
├── specifications.md                # System technical specifications
├── memory.md                        # Architectural decisions, schema definitions, and conventions
├── pytest.ini                       # Test suite configuration
├── requirements.txt                 # Python dependencies
├── .env.example                     # Environment template for LLM provider API keys
├── data/
│   ├── raw/                         # Source documents (6 PDFs + ParcelPilot_Assessment_Data.xlsx)
│   └── processed/                   # SQLite database (parcelpilot.db) & ChromaDB vector store (gitignored)
├── src/
│   ├── config.py                    # Global paths, model configurations, and constants
│   ├── models.py                    # Pydantic data schemas & UserContext dataclass
│   ├── db.py                        # SQLite connection manager & snapshot timestamp helper
│   ├── authority.py                 # 5-Tier Source Authority hierarchy & conflict engine
│   ├── ingest/
│   │   ├── data_loader.py           # Ingests Excel tables into SQLite with snapshot meta
│   │   └── doc_loader.py            # Chunks & embeds PDFs into ChromaDB vector index
│   ├── tools/
│   │   ├── search_documents.py      # Scoped vector document retrieval tool
│   │   ├── query_structured_data.py # Scoped order/ticket/SLA/cancellation calculation tools
│   │   ├── action_tools.py          # Propose & execute state-changing actions
│   │   └── registry.py              # Tool schema definitions & harness dispatcher
│   ├── agent/
│   │   ├── prompts.py               # Customer & Internal persona system prompts
│   │   ├── loop.py                  # Agent reasoning loop & deterministic engine
│   │   ├── llm_providers.py         # Multi-provider LLM chain & circuit breaker
│   │   └── analytics.py             # Proactive SLA alerts & Known Issue correlation
│   └── ui/
│       └── app.py                   # Streamlit web application
└── tests/
    ├── conftest.py                  # Pytest fixtures and test database setup
    ├── test_access_control.py       # Scoping, multi-tenant isolation, and privacy tests
    ├── test_cancellation_and_service_credits.py # Policy vs agreement override math tests
    ├── test_sla_and_actions.py      # SLA calculations & two-phase action tests
    ├── test_eval_queries.py         # End-to-end evaluation scenario tests
    ├── test_llm_providers.py        # Circuit breaker & fallback provider tests
    └── test_token_usage.py          # Token usage tracking tests
```

---

## 🚀 Getting Started & Local Setup

### 1. Prerequisites
- **Python 3.10+** (tested on Python 3.11)
- **pip** and **Git**

### 2. Setup
```bash
# Clone the repository
git clone <repo-url>
cd parcelpilot-ai-agent

# Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate        # On Linux/macOS
# On Windows: .venv\Scripts\activate

# Install required dependencies
pip install -r requirements.txt

# Create your local environment file from example template
cp .env.example .env
```

---

## 🔑 API Keys Configuration

ParcelPilot implements an automatic multi-tier fallback chain: **Groq (`openai/gpt-oss-120b`) → NVIDIA NIM (`nvidia/nemotron-3-nano-30b-a3b`) → Deterministic Multi-Step Reasoner (Offline)**. 

> [!NOTE]
> **Zero API Keys Required for Full Evaluation**: The application runs completely out of the box with zero API keys configured by falling back to the built-in deterministic reasoner (at reduced conversational fluency). Reviewers without API keys can still evaluate all features, data access controls, and test suites.

### Step-by-Step API Key Setup (Optional for Full LLM Generation)

#### Option A: Groq (Primary Provider — Fast & Free)
1. Sign up for a free account at [console.groq.com](https://console.groq.com).
2. Go to **API Keys** in the left sidebar and click **Create API Key**.
3. Copy your key and paste it into `.env`:
   ```env
   GROQ_API_KEY=gsk_your_actual_groq_api_key
   GROQ_MODEL=openai/gpt-oss-120b
   ```

#### Option B: NVIDIA NIM (Secondary Fallback Provider)
1. Sign up for a free NVIDIA developer account at [build.nvidia.com](https://build.nvidia.com).
2. Navigate to any hosted model card (or your profile settings) and generate a personal API key.
3. Copy your key and paste it into `.env`:
   ```env
   NVIDIA_API_KEY=nvapi-your_actual_nvidia_key
   NVIDIA_MODEL=nvidia/nemotron-3-nano-30b-a3b
   ```

> [!WARNING]
> **Security Notice**: Never commit your real `.env` file to version control. The repository `.gitignore` automatically excludes `.env` — only `.env.example` should be tracked in Git.

### How to Verify API Key Configuration
- **Via the UI**: Launch the Streamlit application (`streamlit run src/ui/app.py`), select the **Customer Support** persona, and submit any query (e.g., *"Can Northstar cancel ORD-1001 without a fee?"*). In the sidebar **Token Usage & Telemetry** card, check the active provider tag (shows `Groq` or `NVIDIA NIM` with live token counts when live keys are active).
- **Via Automated Tests**: Run `pytest tests/test_llm_providers.py -v` to verify provider registry parsing and circuit breaker failover.

---

## 📦 Build the Data Layer (Ingestion)

The system parses raw tabular business records from `data/raw/ParcelPilot_Assessment_Data.xlsx` into SQLite and chunks/embeds 6 policy and contract PDFs into ChromaDB:

```bash
# 1. Ingest Excel sheets into SQLite database (populates reference snapshot metadata)
python -m src.ingest.data_loader

# 2. Chunk and embed PDF policies & customer agreements into ChromaDB vector store
python -m src.ingest.doc_loader
```

> **Why `data/processed/` is reproducible**: Derived database files (`data/processed/parcelpilot.db` and `data/processed/chroma_db/`) are excluded from version control to prevent repository bloat and avoid stale indexes. Running the two commands above regenerates the complete structured database and vector store from `data/raw/` in under 30 seconds.

---

## 🖥️ Run the Web Application

Launch the interactive Streamlit user interface:

```bash
streamlit run src/ui/app.py
```

Open your web browser at **`http://localhost:8501`**.

---

## 🧪 Running Automated Tests

Run the complete 29-test test suite covering access control, SLA calculations, agreement overrides, two-phase actions, provider failover, and token telemetry:

```bash
pytest -v
```

### Test Suite Breakdown (29/29 Passing Tests)
- `tests/test_access_control.py` (5 tests): Multi-tenant account scoping, cross-account order/ticket isolation, `UserContext` injection.
- `tests/test_cancellation_and_service_credits.py` (5 tests): SOP v4 cancellation fee calculation, Northstar contract fee waiver override, pickup delay credit math, LumenWorks fixed INR 300 credit override.
- `tests/test_sla_and_actions.py` (5 tests): Deterministic SLA breach tracking, two-phase action staging (`propose_action`), user confirmation execution (`execute_action`).
- `tests/test_eval_queries.py` (7 tests): End-to-end evaluation queries (JD cancellation example, JD service credit example, cross-account denial, authority conflict resolution, TKT-504 webhook delay root cause investigation, TKT-505 security credential leak investigation).
- `tests/test_llm_providers.py` (4 tests): Provider fallback chain, circuit breaker tripping after 3 consecutive failures, automatic recovery, offline deterministic fallback.
- `tests/test_token_usage.py` (3 tests): Prompt/completion token accumulation, rate limit telemetry, and remaining token math.

---

## 🔍 Key Assessment Scenarios Tested

| Scenario | Request / Query | System Behavior & Authority Resolution |
| :--- | :--- | :--- |
| **JD Example 1 (Cancellation Override)** | *"Can Northstar cancel ORD-1001 without a cancellation fee? Explain why."* | Checks `ORD-1001` (`BOOKED`, not yet picked up). Resolves conflict between SOP v4 standard INR 250 fee (Tier 2) and **Northstar Agreement Section 2** (Tier 1) -> Applies **INR 0 Cancellation Fee**. |
| **JD Example 2 (Service Credit)** | *"A pickup is three hours late because of carrier fault. Should I get a service credit?"* | Evaluates pickup delay (3.0h > 2.0h SOP v4 threshold, carrier at fault). Under standard SOP v4 (Tier 2), awards **min(INR 500, 10% shipment fee)**. |
| **LumenWorks Agreement Override** | *"Is ORD-2002 eligible for credit?"* | Evaluates RoadRunner missed pickup (4.5h delay > 4.0h threshold, carrier at fault). Applies **LumenWorks Agreement Section 3** (Tier 1) override -> **Fixed INR 300 Credit**. |
| **Cross-Account Data Isolation** | Customer `ACCT-001` querying `ORD-2001` or `TKT-502` | Enforced at SQL layer (`WHERE account_id = :ctx.account_id`). Returns `AccessDenied` error; zero order/ticket metadata is disclosed. |
| **TKT-504 Root-Cause Investigation** | *"Investigate TKT-504 driver pickup vs. BOOKED status"* | Internal ops audit identifies `KI-211` / SwiftShip webhook 20-minute batching sync delay causing status discrepancy; recommends syncing carrier webhook before escalating. |
| **TKT-505 Security Credential Exposure** | *"Investigate TKT-505: employee exposed production API key"* | Internal ops audit identifies P1 security incident (150m elapsed vs 30m Enterprise SLA = breached); recommends immediate key revocation, log auditing for unauthorized access, public channel message removal, and escalates to Security Lead. |
| **Two-Phase Action Execution** | User confirms staged ticket escalation | Action executes only after explicit user confirmation; updates ticket status in SQLite and marks action executed. |

---

## 🤖 AI Tool Usage Note (Deliverable #6)

- **AI Tools Used**: Claude Code (Claude 3.7 Sonnet) and Antigravity IDE.
- **How AI Tools Were Used**:
  - Designed the type-safe multi-tier Source Authority hierarchy and tool-level `UserContext` injection architecture.
  - Implemented data ingestion pipelines for SQLite database initialization and ChromaDB PDF chunking/embedding.
  - Developed the deterministic offline multi-step reasoning engine to ensure 100% test reproducibility without internet or API keys.
  - Created automated test suites covering edge cases (cross-account privacy, contractual overrides, circuit breaker recovery).
  - Built the Streamlit user interface with live tool traces, token telemetry, citation authority badges, and proactive ops alerts.
