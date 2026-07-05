# Project 6 — Agentic AI: Property-Management Operator Agent

A multi-agent **LangGraph** operator assistant for a small property manager. A
user request (text or an attached image) flows through five nodes — **ingest →
planner → worker → reviewer → dispatch / summarizer** — with a non-bypassable
policy gate, per-user memory, and a tamper-evident hash-chained audit log.

The agent can query tenants, read/reconcile rent ledgers, schedule maintenance
(under a spend cap), and renew leases (with human approval). It **refuses**
anything outside its authority — an eviction or lockout request is hard-blocked,
no tool runs, and the rejection is recorded. That failure case is the rubric's
required limitation demo.

> **Co-designed with Project 7.** The governance layer (`src/governance/`) is
> domain-agnostic: no property-management strings, no `domain` imports. Project 7
> (the SOC security-ops copilot) lifts it verbatim and swaps in a security
> `domain/` package — same graph shell, same policy gate, same audit log. A
> guard test (`test_governance_no_domain_imports.py`) enforces that boundary so
> the transfer cannot break silently.

---

## What this project delivers

| Rubric item | Artifact |
|---|---|
| Agent implementation (init, reasoning, memory, tools, runs) | `src/app.py` + `src/governance/` + `src/domain/` |
| Architecture diagram | `reports/agent_graph.png` (rendered by the notebook) |
| Example runs + failure case | `notebooks/agentic_system.ipynb` (5 scenarios) |
| Short notebook summary | final markdown cell of the notebook |
| System Design Report (with citations) | `reports/Agentic_AI_System_Design_Report.pdf` |
| Reproducibility | `requirements.txt` (curated) + `requirements_full.txt` (full freeze) |

---

## Architecture

### Top-level layout

```
project_06_agentic_ai/
├── src/            # the agent: governance (reusable engine) + domain (property-mgmt specifics)
├── notebooks/      # agentic_system.ipynb — the demo you submit
├── tests/          # 21 passing tests (policy, audit chain, eviction block, governance-leak guard)
├── reports/        # agent_graph.png + the System Design Report (md -> pdf)
├── data/           # generated at runtime: SQLite db, audit log, scratchpad, sample image
├── requirements.txt / requirements_full.txt
└── README.md
```

### The graph

```
START -> ingest -> planner -> worker
worker -> reviewer                       (always, before any tool runs)
reviewer -> {allow: worker_dispatch, require_human: human_approval, block: summarizer}
worker_dispatch -> {more steps: reviewer, done: summarizer}
human_approval -> worker_dispatch
summarizer -> END
```

**The non-bypassable gate:** the worker never calls a tool directly — it always
routes to the reviewer first. The reviewer is a *pure policy gate* (no LLM), so
it cannot be talked into approving a forbidden action. Only its `allow` verdict
reaches `worker_dispatch`, which actually runs the tool.

**The audit log:** every decision, tool call, block, and human approval is
appended to a JSONL file where each line's hash chains to the previous one.
`verify_chain()` recomputes every hash from genesis and raises
`ChainBrokenError` if any past entry was edited — the trail is tamper-evident.

The diagram is regenerated inside the notebook (and saved to
`reports/agent_graph.png`):

```python
build_system(use_llm=False).graph.get_graph().draw_mermaid_png()
```

### Source layout (`src/`)

The agent is split into a **reusable engine** and a **swappable domain** — this
is the central design decision (see "The governance→P7 transfer" below).

```
src/
├── governance/          # REUSABLE IN P7 — domain-agnostic
│   ├── graph_state.py   # AgentState, PlanStep, ActionIntent, ReviewDecision, ToolResult
│   ├── graph_builder.py # build_graph(...) -> compiled LangGraph
│   ├── nodes.py         # planner/worker/reviewer/dispatch/summarizer/human_approval + routers + parsers
│   ├── policy.py        # YAML policy loader + constraint-predicate evaluator (gt/ge/lt/le/in/regex_match)
│   ├── audit.py         # AuditLogger — hash-chained JSONL, verify_chain()
│   ├── memory.py        # SessionScratchpad — per-user SQLite (no vector store)
│   └── pii.py           # regex redactor driven by the policy
├── domain/              # P6-SPECIFIC — P7 replaces this whole package
│   ├── intake_node.py   # OCR (pytesseract) + vision fallback (kimi-k2.7-code) + PII redaction
│   ├── tools.py         # tenant_query, ledger_read/reconcile, maintenance_schedule, lease_renew
│   ├── policy.yaml      # the rules (spend cap, required fields, eviction hard-block, PII patterns)
│   ├── schema.sql       # tenants / units / leases / maintenance / inbox
│   └── prompts.py       # planner/worker/summarizer system prompts
└── app.py               # wires governance + domain, CLI + run_scenario()
```

The notebook imports from `src/` (`sys.path.insert(0, str(SRC))`), so the
governance/domain split is what the notebook actually loads — not just a
documentation convention.

---

## Prerequisites

1. **Python 3.11+** (built and tested on 3.12).
2. **Ollama Cloud credentials** for the `kimi-k2.7-code:cloud` model:
   - `OLLAMA_API_KEY` — your Ollama Cloud key
   - `OLLAMA_BASE_URL` — `https://ollama.com`
   - Put them in `project_06_agentic_ai/.env` (gitignored):
     ```
     OLLAMA_API_KEY=...
     OLLAMA_BASE_URL=https://ollama.com
     ```
   - Without these, the notebook runs in **stub mode** (deterministic, scripted
     plans) for scenarios 1–4. Scenario 5 (the LLM image run) skips with a clear
     message.
3. **Tesseract OCR binary** (optional) — `pytesseract` is a wrapper; the binary
   must be installed separately.
   - Windows: [UB-Mannheim Tesseract installer](https://github.com/UB-Mannheim/tesseract/wiki) or `choco install tesseract`
   - If missing, the intake node skips OCR and falls back to the `kimi-k2.7-code`
     vision model (when LLM credentials are present). The notebook still runs.

---

## Setup

```powershell
cd D:\AI_Master\Udacity\capstone_projects\project_06_agentic_ai

# Create + activate a virtualenv (or reuse an existing one)
python -m venv p6_env
.\p6_env\Scripts\Activate.ps1          # bash: source p6_env/Scripts/activate

# Install dependencies
pip install -r requirements.txt        # curated direct imports
# — or —
pip install -r requirements_full.txt   # exact pinned environment (pip freeze)
```

---

## Run

### Tests (21 passing)

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m pytest tests/ -v
```

Single file:
```powershell
python -m pytest tests/test_eviction_blocked.py -v
```

### Notebook

```powershell
jupyter lab notebooks/agentic_system.ipynb
```
Then **Run All**. Scenarios 1–4 run in stub mode (no LLM); scenario 5 runs the
real `kimi-k2.7-code:cloud` on `data/sample_request.png` when `.env` is present.

Or execute headlessly:
```powershell
python -m nbconvert --to notebook --execute --output agentic_system.ipynb notebooks/agentic_system.ipynb
```

### CLI

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m src.app --scenario eviction          # stub mode
python -m src.app --scenario maintenance --llm # real LLM (needs .env)
```
Scenarios: `readonly`, `maintenance`, `lease`, `eviction`.

---

## Scenarios

| # | Scenario | What it shows |
|---|---|---|
| 1 | **Read-only happy path** | `tenant.query` auto-allowed (read-only skips the human branch) |
| 2 | **Side-effect with spend cap** | `maintenance.schedule` allowed because `cost_estimate ≤ 500` |
| 3 | **Human approval** | `lease.renew` routes through the human-approval node (auto-granted in demo) |
| 4 | **FAILURE CASE — eviction/lockout** | `tenant.lockout` hard-blocked; no tool called; audit `block` entry |
| 5 | **Multi-modal LLM run** | `kimi-k2.7-code:cloud` reads `sample_request.png`, plans, executes |

---

## The governance→P7 transfer (Path C)

The reviewer's constraint check is a small predicate evaluator
(`gt`/`ge`/`lt`/`le`/`in`/`regex_match`) over the YAML. P6's spend cap
(`cost_estimate: {le: 500}`) and P7's risk-band threshold
(`risk_band_score: {ge: 75}`) are the **same mechanism** — that is what lets the
governance layer stay domain-agnostic.

| P6 (property management) | P7 (security operations) |
|---|---|
| `maintenance.schedule` + spend cap | `incident.escalate` + risk threshold |
| `lease.renew` requires human | `risk_band=critical` requires human |
| `tenant.evict` hard-blocked | `case.close` human-only |
| YAML policy, hash-chain audit, LangGraph shell | reused verbatim |

The guard test `test_governance_no_domain_imports.py` fails if any governance
file imports `domain.*` or contains property-management string literals —
guaranteeing the P7 lift can't break silently.

---

## Reproducibility

- `requirements.txt` — curated direct imports.
- `requirements_full.txt` — the exact pinned environment (`pip freeze`).
- Synthetic data is deterministic: `domain/tools.py` seeds the SQLite DB and CSV
  ledgers on first run.
- The notebook is Restart & Run All clean.

---

## Known limitations

- **Human-approval auto-grants in demo mode.** A production deployment would
  call LangGraph's `interrupt` and block until a human responds.
- **Worker tool selection in LLM mode is loose** (`tool_specs=[]`): the planner
  produces distinct steps, but the worker's per-step arg-filling can drift to the
  read-only tool. The reviewer still gates every step correctly; binding real
  tool specs is the documented next step.
- **OCR requires the Tesseract binary.** Without it, intake falls back to the
  vision LLM (when credentials are present).

---

## Project context

This is **Project 6** of the Udacity AI Master capstone — an eight-project
sequence whose final synthesis (P7) is the SOC security-ops copilot. P6 is
co-designed with P7 as the governance-pattern donor.