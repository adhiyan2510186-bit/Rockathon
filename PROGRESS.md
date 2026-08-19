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
| 8 | `agent/weights.py` | 2 — phrase label -> weights, pure Python | DONE |
| 9 | `data/` mock catalogs + `agent/discovery.py` | 3 — two schemas -> one Product | DONE |
| 10 | `agent/ranking.py` + `tests/test_ranking.py` | 4 — the golden 58.0/48.7/33.7 | **DONE — 14 tests green** |
| 11 | `agent/escalation.py` | one handler, four call sites | **DONE** |
| 12 | `agent/authorisation.py` | 5 — the limit check | **NEXT** |
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

### Stage 3-7: the one escalation handler (`agent/escalation.py`)

One public function, `handle(context, trigger, audit, ...)`, called from four
places. Four call sites, ONE mechanism — CLAUDE.md forbids four separate code
paths, because a boundary implemented four times is four chances to draw it in
four different places.

| Call site | Trigger enum | CLAUDE.md trigger | Outcome on the demo data |
|---|---|---|---|
| stage 3, nothing passed | `NO_ELIGIBLE_MATCH` | 1 | escalate, 3 near-misses + relaxation proposals |
| stage 5, over the limit | `OVER_AUTHORISATION_LIMIT` | 2 | escalate, Rs 4,500 over, KraftPro offered as best in-limit |
| stage 6, unavailable | `UNAVAILABLE_AT_CONFIRMATION` | 3 | escalate — the gap is 9.3, over the 5-point threshold |
| stage 7, payment declined | `PAYMENT_DECLINED` | 3 | escalate for the same reason |

Four enum names, three triggers: stages 6 and 7 are the same situation ("the
option we picked cannot be bought") found at two different stages, so they share
one handler and only the logged stage differs.

Every path runs the same five steps: DETECT, RE-VALIDATE, RELAX, RE-RANK &
SURFACE, ESCALATE & LOG.

**The only time the agent acts alone** is a trigger-3 fallback where BOTH hold:
the next option passes an independent re-check of every hard gate, and it trails
by no more than 5 points. Verified both ways — with the real 9.3-point gap it
escalates; with a 3-point gap it swaps and logs a FALLBACK instead.

Two details worth defending:

- **Re-validation reuses `discovery.apply_hard_gates`**, it does not write a
  quick check of its own. Two definitions of "eligible" in one codebase is
  exactly how an ineligible product ends up bought.
- **Near-misses are compared as FRACTIONS of the user's own limit**, not raw
  numbers. Rs 2.50 over a Rs 22 cap (11%) is a smaller ask than 2 days over a
  10-day window (20%), and raw numbers would have said the opposite.

What it will never do: relax category / quantity / specs, apply any relaxation
itself (they are text for a human), raise the authorisation limit, or treat
silence as approval — every escalation parks the transaction in
`AWAITING_APPROVAL` with its state intact.

Verified against the demo brief: escalation at stage 5 shows Rs 1,09,500 vs the
Rs 1,05,000 limit with KraftPro (Rs 1,04,500) as the no-approval-needed
alternative; and with the cap dropped to Rs 15 so nothing passes, stage 3
surfaces EcoMail (17% over), ValuePack (20% late) and KraftPro (39% over) in
that order, flipping to delivery-first when a flexibility order is declared.

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

### How the weights are produced (confirmed against the golden table)

`weights.compute(brief)` in four steps, all plain Python:

1. Start from the category defaults (packaging: 0.25 / 0.30 / 0.25 / 0.20).
2. Substitute the stated priority: `matters_a_lot` -> 0.45, leaving 0.55.
3. Rescale the other three proportionally: 0.2200 / 0.1833 / 0.1467.
4. **Round to 0.05** -> price 0.20, replacement 0.20, delivery 0.15.

**Step 4 is load-bearing, not cosmetic.** Proportional rescaling ALONE gives
0.22 / 0.1833 / 0.1467, which does not match the deck. The rounding is what
lands us on 0.45 / 0.20 / 0.20 / 0.15. The step lives in `config.yaml` as
`weight_rounding_step: 0.05` — change it and the golden ranking test goes red.

Verified: all 18 combinations tested (6 priority patterns x 3 categories) sum to
exactly 1.0, including the awkward ones (three criteria at "matters a lot",
unknown phrase labels, all four stated).

### Stage 3: what the two catalogs look like, and what survives

Seven products, two shapes. The aggregator disagrees with the direct feed on
every column, which is what makes the normaliser real work:

| PackHub (direct JSON) | BoxBazaar (aggregator CSV) | conversion |
|---|---|---|
| `unit_price_inr: 21.90` | `rate_paise: 1760` | / 100 |
| `lead_time_days: 4` | `ship_window: "7-9 days"` | take the LATE end |
| `seller_rating: 4.8` | `vendor_score_100: 82` | / 20 |
| `replacement_window_days: 7` | `returns_policy: "30-day replacement"` | pull the number |
| `attributes: [...]` | `spec_blob: "DW \| 200 x 150 ..."` | split, expand "DW" |

Taking the LATE end of a shipping window is a deliberate judgement call, in
`_worst_case_days()`: a 10-day deadline is only genuinely met if the latest date
clears it.

**Three survive, four are rejected — one per reason**, which is what makes the
filter demonstrable rather than decorative:

- Corusafe DW, KraftPro DW, EcoMail DW -> PASS (the golden three)
- MegaBox DW -> Rs 24.50, over the Rs 22 cap by Rs 2.50 (near-miss, price)
- ValuePack DW -> 12 days against a 10-day window (near-miss, delivery)
- CraftMail DW -> 3,000 in stock against 5,000 needed (fails quantity)
- SingleWall Lite -> not double-wall (fails a non-negotiable spec)

The two near-misses are deliberate raw material for the escalation handler.

**The spec-spelling problem is solved** in `discovery.spec_key()`: it lowercases,
strips spaces and punctuation, and expands trade shorthand. Verified that
`["double-wall","200x150x80mm"]`, `["double-wall","200x150x80 mm"]` and
`["DW","200 x 150 x 80 mm"]` all produce the same three survivors.

Chain 1 -> 3 verified end to end offline: weights 0.45/0.20/0.20/0.15, three
survivors matching the deck exactly, order total Rs 1,09,500.

### How normalisation works (confirmed against the golden table)

Two different methods, and mixing them up breaks the numbers:

- **reliability, replacement** -> min-max across the surviving pool
  (best = 1.0, worst = 0.0)
- **price, delivery** -> `sqrt(margin vs the hard cap)`, where
  `margin = (cap - value) / cap`. The square root is why a bargain cannot
  drown out what the user said mattered.

---

## The golden test is live: `python -m pytest tests/ -q`

**14 tests, all green.** Run this before every commit that touches ranking,
weights, config or the catalogs.

It covers the whole chain that produces the table — catalogs, normaliser, hard
gates, weight engine, ranker — because any one of them can move a score. It uses
the offline parser deliberately: no API key, no network, no rate limit, same
answer every time.

Beyond the three scores it also locks: the ranking order, the winner's four
terms, reliability contributing 0.450 of Corusafe's 0.580, the 9.3-point gap,
the weights, 3-of-7 surviving with four distinct rejection reasons, the
Rs 1,09,500 order total, determinism across five runs, and independence from
catalog file order.

**The tripwire is verified.** Changing `weight_rounding_step` from 0.05 to 0.01
turns 4 tests red; restoring it turns them green. The test can actually fail,
which is the only thing that makes a passing test mean anything.

### The behaviour test worth knowing about

`test_cheapest_product_ranks_last` asserts the pitch as behaviour, not as a
number: EcoMail is 20% under the cap and finishes last. If a future change ever
lets the bargain win, that test fails and tells us the demo has quietly stopped
making its point.

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

## How narrow are we really? (tested 2026-08-19, say this before a judge finds it)

We tested the parser on deliberately awkward briefs, not just our demo sentence.
The answer has two halves and the second half is the one that matters.

### Wording: the Gemini path is genuinely flexible

Nobody wrote a rule for any of these. The model handled them:

| Written as | Understood as |
|---|---|
| "nothing over **twenty rupees** each" | cap = 20.0 |
| "delivered in **3 weeks**" | 21 days |
| "just **don't send junk**" | reliability: matters |
| "**don't care much** about speed" | delivery: nice_to_have |

So "does it only work on the sentence you rehearsed?" -> no, and that is the
entire reason there is an LLM in the project.

### Wording: the OFFLINE fallback is brittle, and one failure is not harmless

Same chatty brief, offline parser: it missed "twenty rupees" (digits only), missed
"end of next week", and missed every priority.

The cap miss is the one to watch. A missed cap becomes the Rs 25 packaging
default, so a user who said Rs 20 could be shown products at Rs 24. It IS tagged
ASSUMED and written to the audit log, so it is visible rather than hidden - but
visible-and-wrong is still wrong. If we lose the API key mid-demo, that is the
degradation we are accepting.

### The real narrowness is the DATA, not the language

An "office chairs" brief parsed perfectly - 300 units, Rs 8000 cap, 21 days,
warranty matters - and would then find zero vendors, because our catalogs are
packaging only. The bottleneck is not phrasing:

1. Mock catalogs contain packaging only.
2. config.yaml knows `packaging` and `labels`; anything else gets the generic
   Rs 50 cap and four equal weights.
3. Four soft criteria, three strength labels. "Must be carbon-neutral" has
   nowhere to go.
4. One brief = one product line. "500 boxes and 2000 labels" is not supported
   (that is the "designed, not demoed" line in CLAUDE.md).

### The sentence to say out loud

> "Wording - we handle a lot of it, because that is the one job the LLM does.
> Scope - we are deliberately narrow: one product line, packaging category, mock
> catalogs. The narrowness is in the data, not the language."

### Looked like a bug, is not

The chatty brief lost its deadline ("end of next week" is not a number of days).
That does not break anything: stage 0 returns `incomplete` and the agent asks one
question rather than guessing a date. Designed behaviour, working.

---

## Working agreement

- One file at a time, explained in plain terms after each.
- Commit and push every working state to `origin/master` without being asked.
- Anything touching the ranker must keep the golden test green.
- Build only the demo path in CLAUDE.md. Everything else is
  "designed, not demoed".
