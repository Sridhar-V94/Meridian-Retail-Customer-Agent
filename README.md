# Meridian Retail Support-Draft Review Tool

Smallest useful CLI tool to prepare a **support-draft review** for Meridian Retail cases using the existing `policy.md` and `cases.json`.

## Scope and guardrails
- Drafts are for **human review only**.
- The tool **never** sends a message, approves a refund, or changes a record.
- Correct JSON structure from the model does **not** prove correct content.
- No web UI, database, RAG system, login flow, agent framework, or production integration.

## Files
- `app.py` — main CLI application
- `setup_env.py` — creates a project-only `.env`
- `.env.example` — placeholder environment variables
- `demo_outputs.json` — labelled prerecorded synthetic results for offline demo
- `policy.md`, `cases.json` — supplied inputs (existing)

## Prerequisites
- Python 3
- [OpenCode CLI](https://opencode.ai) installed and configured for live draft generation (`opencode run`). Live runs may take some time depending on the configured OpenCode model/provider.
  - Offline demo and simulate-timeout modes do **not** require a live model call.

## Setup
1. (Optional) create and activate a virtual environment.
2. Install pinned dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. (Optional) Configure environment (writes project-only `.env`):
   ```bash
   python setup_env.py
   ```
   Or copy `.env.example` to `.env`.

Environment variables used by the app:
- `APP_OPENCODE_MODEL` — optional model override (e.g. `provider/model` or `provider/model#variant`). If unset/blank, OpenCode's configured default model is used.

The app does **not** read or set `APP_ANTHROPIC_API_KEY`, `ANTHROPIC_API_KEY`, or any other global API key. No application API key is required.

## Usage
Run for a specific case (`C1`, `C2`, or `C3`):

```bash
python app.py --case C1
```

The output includes three labelled sections:
- `SUPPLIED FACTS`
- `SUPPLIED POLICY`
- `DRAFT RESULT FOR HUMAN REVIEW`

### Offline demo (no model/API call)
Uses a labelled prerecorded synthetic result from `demo_outputs.json` and prints an explicit line stating no model API was called:

```bash
python app.py --case C1 --offline-demo
```

### Simulated timeout (no model/API call)
Produces a safe unavailable state with a manual fallback message:

```bash
python app.py --case C1 --simulate-timeout
```

## Failure behavior
If the provider/model response fails, times out, or returns invalid output, the app shows the same safe unavailable state (`review_status: UNAVAILABLE`, cleared draft, manual fallback message) rather than a traceback or stale draft.

## Tests
Tests do not call an external API or external model CLI:

```bash
pytest -q
```
