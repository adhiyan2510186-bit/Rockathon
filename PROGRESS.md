# PROGRESS.md — where we are, so we can restart cold

Read CLAUDE.md first (the rules). Read this second (the state).
Last updated: 2026-08-19.

---

## How to get running again

```
python -m pip install -r requirements.txt
```

The Gemini API key lives in `.env` (NOT in git — copy `.env.example` and paste
the key in). If `.env` is missing, the language step falls back to the offline
parser and the UI says so plainly.

---

## Build order (stage 0 -> 8) and status

| # | File | Stage | Status |
|---|------|-------|--------|
| 1 | `config.yaml` | 2 & 5 source of truth | DONE |
| 2 | `requirements.txt` | — | DONE (5 libraries, all installed) |
| 3 | `.gitignore`, `.env.example` | — | DONE (key stays out of git) |
| 4 | `agent/config.py` | reads config.yaml, nowhere else does | DONE |
| 5 | `agent/models.py` | shared data shapes for all stages | DONE |
| 6 | `agent/audit.py` | 8 — append-only logger | DONE |
| 7 | `agent/language.py` | 0-2 — the only file that calls Gemini | DONE |
| 8 | `agent/weights.py` | 2 — phrase label -> weights, pure Python | **NEXT** |
| 9 | `data/` mock catalogs + `agent/discovery.py` | 3 — two schemas -> one Product | to do |
| 10 | `agent/ranking.py` + `tests/test_ranking.py` | 4 — the golden 58.0/48.7/33.7 | to do |
| 11 | `agent/escalation.py` | one handler, four call sites | to do |
| 12 | `agent/authorisation.py` | 5 — the limit check | to do |
| 13 | `agent/vendor.py`, `agent/payment.py` | 6-7 — mocks with failure switches | to do |
| 14 | `app.py` | Streamlit, four screens | to do |

`audit.py` is done, so every stage from here on can write its own log line as
it acts instead of being retrofitted later. `language.py` is next: it is the
only file that touches Gemini, and stages 0-2 are the front of the demo run.

What `audit.py` gives the rest of the build:

- `AuditLogger(context)` — one logger per transaction, passed to every stage.
  Five methods named after the five event types: `decision`, `assumption`,
  `escalation`, `fallback`, `action`.
- Stage label constants (`STAGE_RANKING` etc.) so nine stages spell the stage
  names the same way.
- Entries flush to `exports/TXN-####.jsonl` the instant they are created — a
  crash at stage 6 still leaves stages 0-5 on disk.
- `finance_view()` renders the same entries as the one-page auditor view, and
  `replay(transaction_id)` reads a finished run back from disk.

`language.py` is done and verified against the live API on 2026-08-19. It is the
only file that imports the Gemini SDK. Two public functions:

- `check_scope(text, audit)` — stage 0. Returns out_of_scope / incomplete /
  in_scope. The one clarifying question is logged as a DECISION, not an
  ESCALATION: escalation stays reserved for the three stage-5 triggers.
- `extract_brief(text, audit)` — stage 1. Returns a filled `Brief`. A missing
  per-unit cap is filled from config and tagged ASSUMED, logged the moment it
  is applied.

Both return `.source` ('gemini' or 'offline') and `.note`, which the UI prints.
Online and offline both produce the same `_Extraction` and go through the same
`_to_brief()`, so the fallback cannot behave differently — only the reading of
the sentence changes.

`config.py` gained one accessor for this: `priority_phrase_labels()`, returning
the phrase KEYS only. That is the single thing from config.yaml the model is
shown, and it is names without numbers.

### Verified on the demo brief (both parsers agree)

category packaging · quantity 5000 · cap Rs 22 · 10 days ·
`stated_priorities = {"reliability": "matters_a_lot"}` — which is what
`weights.py` turns into reliability 0.45.

### One thing to handle in discovery.py

The two parsers spell dimensions slightly differently: Gemini returns
`"200x150x80 mm"`, the offline parser returns `"200x150x80mm"`. Spec matching at
stage 3 must strip spaces and lowercase before comparing, or an identical box
fails a hard gate on a space.

---

## Numbers that must not drift

These come from the deck. If code disagrees with them, the code is wrong.

- Ranking: **Corusafe 58.0 / KraftPro 48.7 / EcoMail 33.7**
- Corusafe's arithmetic: `0.45x1.000 + 0.20x0.067 + 0.15x0.775 + 0.20x0.000 = 0.580`
- Weights for the demo brief: reliability 0.45 (user-stated), price 0.20,
  replacement 0.20, delivery 0.15
- Score gap #1 to #2: **9.3 points** (over the 5-point substitution threshold,
  so the agent escalates rather than swapping)
- Demo order total: 5,000 x Rs 21.90 = **Rs 1,09,500**, which is over the
  Rs 1,05,000 authorisation limit — this is what makes the approval screen fire

### How normalisation works (confirmed against the golden table)

Two different methods, and mixing them up breaks the numbers:

- **reliability, replacement** -> min-max across the surviving pool
  (best = 1.0, worst = 0.0)
- **price, delivery** -> `sqrt(margin vs the hard cap)`, where
  `margin = (cap - value) / cap`. The square root is why a bargain cannot
  drown out what the user said mattered.

---

## Decisions already made (do not re-litigate)

- **Model: `gemini-3.6-flash`.** `gemini-2.5-flash` returns 404 for new API
  keys — Google redirects callers to the 3.x line. Verified 2026-08-19.
- **Free tier is roughly 10 requests/minute**, hence the offline fallback
  parser. We degrade honestly and label it in the UI; we never fake a call.
- **Constraint classes are a fixed table in `models.py`**, not something the
  LLM decides per run. The LLM finds values; the table says what values are for.
- **Price is deliberately both a hard gate (stage 3) and a soft criterion
  (stage 4).** Two questions, one measurement. Not double counting.
- **No FastAPI, no servers.** Vendor and payment mocks are plain Python modules
  with failure-injection flags, flipped from the Streamlit sidebar.

---

## Working agreement

- One file at a time, explained in plain terms after each.
- Commit and push every working state to `origin/master` without being asked.
- Anything touching the ranker must keep the golden test green.
- Build only the demo path in CLAUDE.md. Everything else is
  "designed, not demoed".
