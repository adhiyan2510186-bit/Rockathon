# CONTEXT.md — project briefing for an AI assistant

Attach this file to any chatbot and it will know what this repository is, how it
is built, and what state it is in. Everything below was read off the code on
**2026-08-21**, at commit `ab9d977`, with all tests passing.

If you are an assistant reading this: `CLAUDE.md` in the same folder holds the
binding design rules. This file is the *situation report* — what exists, what
works, what is deliberately missing. When the two disagree, `CLAUDE.md` wins and
you should say so.

---

## 1. What this is

**Autonomous Commerce Engineering Agent — Team E-Vets** (hackathon project).

A plain-language buying brief goes in; an audited, human-approved procurement
decision comes out — with the agent knowing exactly where its own authority ends.

Demo brief (the one every number in this repo is tuned to):

> "5,000 kraft mailer boxes, double-wall, 200x150x80 mm, max Rs 22 per unit,
> delivered within 10 days. Reliability matters a lot — we got burned last
> quarter."

Tagline: **autonomy the user can audit.**

Persona: Meena, ops manager at a Chennai D2C skincare brand. She is who we design
*for* — **no screen ever prints her name**. The UI and the audit trail say
`User` for the human side and `Agent` for the agent's side.

---

## 2. THE ONE RULE (violating this is a bug, not a feature)

**The LLM interprets language. It never decides the purchase.**

- The model is touched in exactly one file — `agent/language.py` — for stages
  0–2 only (scope gate, requirement extraction, priority reading).
- Every number a judge could question — score, margin, limit check, ranking,
  market signal — is plain Python. Same brief in, same ranking out, every run.
- Authorisation limits live in `config.yaml`, never in a prompt. No wording in a
  user's brief can talk the agent past a limit, because the agent never sees the
  limit as instruction.

If a request would put ranking, scoring, limit checks or urgency inside the LLM:
stop and flag it.

---

## 3. Current status — verified, not planned

| Thing | State |
|---|---|
| Test suite | **257 tests, all passing**, ~21 s, no network (`python -m pytest -q`) |
| Demo path (9 steps, section 8) | **Complete and runnable end to end** |
| Golden ranking numbers (58.0 / 48.7 / 33.7) | **Green**, asserted in `tests/test_ranking.py` |
| Git | branch `master`, clean tree, pushed to `origin/master` (github.com/adhiyan2510186-bit/Rockathon) |
| Safety net | tag **`v1-demo-frozen`** — a known-good runnable demo. Never delete it |
| App | Streamlit, dark theme forced, four screens, runs on :8501 |
| Catalog | **57 products, 6 sources, 4 stocked categories** (packaging 17, laptops 16, headphones 14, furniture 10) |
| LLM | Gemini (`gemini-3.5-flash-lite`), optional — offline deterministic parser fallback works and says so on screen |

Recent work (last ~10 commits) has been UI and presentation polish: stylesheet
split into its own file, layered dark canvas, pill tags, the outcome trail
redrawn as a trail, the audit record made inspectable, and every docstring
brought back in line with what the code now does.

---

## 4. How to run it

```bash
python -m pip install -r requirements.txt
streamlit run app.py            # http://localhost:8501
python -m pytest -q             # 257 tests, offline, ~21s
```

An API key is optional. `cp .env.example .env`, paste a Gemini key. Without one,
briefs are read by a deterministic word-matching parser and the UI states that.
`config.yaml` → `llm.use_model: false` switches the model off for rehearsal so
practice runs cost nothing.

---

## 5. Architecture — stages 0 to 8

Data flows 0 → 8. A shared transaction context and an append-only audit logger
run alongside every stage; no state is reconstructed after the fact.

| Stage | What happens | File |
|---|---|---|
| 0 | Scope & completeness gate. Off-topic declined; an incomplete brief gets ONE targeted question | `agent/language.py` |
| 1 | Requirement extraction, LLM → JSON, each field tagged `hard`/`soft`/`ambiguous` | `agent/language.py` |
| 2 | Preference & weight engine — stated priorities → weights summing to 1 | `agent/weights.py` |
| 3 | Vendor discovery & hard-constraint gate (pass/fail) | `agent/discovery.py`, `agent/sources.py` |
| 4 | Ranking — weighted score over soft criteria. Pure Python | `agent/ranking.py` |
| 4.5 | Market signal — timing/urgency read. **Advisory only** | `agent/signals.py` |
| 5 | Decision & authorisation — proceed alone, or escalate on 3 triggers | `agent/authorisation.py` |
| 6 | Vendor confirmation & lock — re-validate price and stock | `agent/vendor.py` |
| 7 | Mock payment — retry once, then fall back | `agent/payment.py` |
| 8 | Confirmation & audit close | `agent/close.py`, `agent/audit.py` |
| — | The ONE escalation handler, entered from four places | `agent/escalation.py` |

**Source adapter layer.** Discovery does not know where products come from.
`SourceAdapter.fetch() -> list[Product]`, six adapters over three `read()`
shapes: `PackHubAdapter` / `OfficeStockAdapter` (direct vendor JSON),
`BoxBazaarAdapter` / `TradeBridgeAdapter` (aggregator CSV — paise, ship ranges,
score /100, returns as a sentence), `AmazonAdapter` / `FlipkartAdapter`
(marketplace JSON — nested rating object, its own delivery wording). That ratio
is the point: a new source of a shape we already handle is a subclass and a file
path. The UI's source toggle demonstrates it live.

---

## 6. File map (line counts at this commit)

```
app.py                 1273   The interface. Four screens. Shows things, passes button
                              presses through. Computes NOTHING — every number is read
                              off an object some stage already produced.
agent/
  language.py           959   Stages 0-2. THE ONLY file that talks to a model.
  escalation.py         885   The one escalation handler (4 call sites, 1 mechanism).
  config.py             631   The only doorway to config.yaml. Only file in agent/
                              allowed to name a category.
  models.py             560   Pydantic shapes shared by every stage.
  sources.py            507   Source adapter layer. 6 adapters, 3 read() shapes.
  vendor.py             473   Stage 6 confirm & lock.
  payment.py            464   Stage 7 mock payment (injectable failures).
  signals.py            463   Stage 4.5 market signal.
  weights.py            419   Stage 2 weight engine.
  authorisation.py      402   Stage 5 authority boundary.
  audit.py              391   Append-only logger, one wrapper schema.
  discovery.py          266   Stage 3 hard gate.
  ranking.py            230   Stage 4. The file the whole project defends.
  close.py              162   Stage 8 close-out.
ui/
  styles.py             888   The stylesheet, one string.
  components.py         547   Reusable screen pieces.
  theme.py              475   Colour, type, spacing. One source of truth.
  charts.py             259   Three Plotly charts — each for a shape a table shows badly.
tests/                        13 files, 257 tests.
data/                         6 catalogs: 2 direct JSON, 2 aggregator CSV, 2 marketplace JSON.
exports/                      15 sample audit trails, one JSONL file per transaction.
docs/demo-runbook.html        The run-of-show for presenting.
.streamlit/config.toml        Dark theme declared once (not left to the viewer's OS),
                              tracebacks hidden on stage, toolbar in "viewer" mode.
```

Every module carries a long "WHY THIS FILE EXISTS" docstring, written for us and
for judges reading the repo. Read the docstring before changing the file.

`app.py` screens: **Request** (what was asked, what we understood) → 
**Recommendation** (headline, comparison, market signal) → **Approval** (the
human gate, the outcome trail, the order record) → **Activity** (the audit log
read back as a finance view).

---

## 7. The frozen numbers

### Ranking formula (Stage 4)

```
score = SUM( weight × normalised )   over the four soft criteria only
```

Hard gates have already filtered the pool, so scoring runs over survivors only.
Margins use `sqrt(margin)`, so a bargain cannot drown out what the user said
mattered.

**Golden table for the demo brief.** Weights: reliability 0.45 (user-stated),
price 0.20, replacement 0.20, delivery 0.15.

| Product · source | Rs/unit | Days | Reliab. | Replace | Score |
|---|---|---|---|---|---|
| Corusafe DW · PackHub (direct) | 21.90 | 4 | 4.8 | 7 d | **58.0** |
| KraftPro DW · PackHub (direct) | 20.90 | 6 | 4.6 | 10 d | **48.7** |
| EcoMail DW · BoxBazaar (aggregator) | 17.60 | 9 | 4.1 | 30 d | **33.7** |

Winner's arithmetic: `0.45×1.000 + 0.20×0.067 + 0.15×0.775 + 0.20×0.000 = 0.580 → 58.0`.

The cheapest option (20% under cap) ranks LAST because the user said reliability
matters. The formula has no favourite; the brief does. **Any change touching the
ranker must keep this test green.** Stage 4.5 must not move these by a hair.

### The two limits (different questions — do not conflate)

- **Per-unit cap** — a HARD constraint, enforced by the filter at stage 3. The
  demo brief states Rs 22; `config.yaml` holds per-category defaults (packaging
  Rs 25). No product above the cap is ever considered.
- **Authorisation limit — Rs 1,05,000** (`config.yaml: authorisation_limit_inr`)
  — total spend the agent may commit without a human, checked at stage 5.
  Exceeding it **escalates**; it does not reject.

Price appears in both roles on purpose: a hard gate at stage 3 ("does it
qualify?"), then a margin-derived soft criterion at stage 4 ("how much do we
prefer it among qualifiers?"). Reuse of one measurement, not double counting.

### Other frozen constants (all in `config.yaml`)

- `substitution_threshold_points: 5` — if #1 leads #2 by more than 5 points and
  #1 becomes unavailable, escalate rather than silently swap. In the demo the gap
  is 9.3 points.
- `market_signal:` `act_now_cover_days 3`, `order_soon_cover_days 7`,
  `material_price_move_pct 3.0`.
- `demo_failure_injection: decline_first_payment_attempt: true` — this is what
  produces the demo's decline-then-retry-then-confirm.
- `priority_phrase_weights`, `weight_rounding_step: 0.05` — how "matters a lot"
  becomes 0.45.

---

## 8. The demo path (this is the whole deliverable)

One unbroken run, all of it working today:

1. An off-topic question is refused, scope stated.
2. A real brief is parsed into hard / soft / ambiguous fields.
3. Vendor sources with different schemas are normalised — source toggle shown working.
4. Ranked comparison shown as a decision; the score breakdown available **on demand**.
5. Market signals surfaced alongside the recommendation, clearly advisory.
6. Order exceeds the authorisation limit → approval screen.
7. User approves → vendor confirmation re-validates price.
8. Payment declines once → retry → confirmed.
9. Audit log exported and read back as the finance view.

**Designed, not demoed — do not build these:** live vendor API adapter (the seam
exists, the implementation does not), three-briefs-at-once / cross-brief bundle
discounts, 24h real expiry, real payment rails.

---

## 9. Rules that constrain any change

**Escalation — exactly three triggers, nothing else:**

1. No product passes all hard gates → relax negotiable constraints only, in the
   user's declared flexibility order; surface 2–3 near-misses with deltas.
2. Valid match but order total over the authorisation limit → no purchase, state
   held; show overage + why still recommended + best in-limit alternative.
3. Unavailable at confirmation OR payment declined → auto-fall-back to next
   eligible product, independently re-validated; escalate if the score gap
   exceeds 5 points or the fallback also fails.

Deliberately **not** triggers: the top pick being imperfect on a soft preference
(ranking absorbs that — escalate on it and every order escalates), and **any
market signal**.

**The Stage 4.5 guardrail.** A market signal must never alter eligibility, score,
ranking, or the authorisation decision. It cannot move a product past a hard
gate, add a point of score, reorder the ranking, or justify spend above the
limit. "Buy now" makes a human decide *sooner*; it never makes the agent decide
*alone*. This is the first thing a sharp judge probes.

**Silence is never approval.** A pending request expires rather than proceeding.
State is preserved, so approving later resumes at stage 6 — discovery and the LLM
never re-run.

**One escalation mechanism, four call sites** (stage 3 no match, stage 5 over
limit, stage 6 unavailable/price drift, stage 7 payment failure ×2):
DETECT → RE-VALIDATE → RELAX → RE-RANK & SURFACE → ESCALATE & LOG. Never four
separate code paths.

**Audit schema — one wrapper for every entry:** `entry_id`, `transaction_id`,
`timestamp`, `stage`, `event_type`, `actor`, `detail`, `reasoning`, `notify`.
`event_type` ∈ `DECISION | ASSUMPTION | ESCALATION | FALLBACK | ACTION |
MARKET_SIGNAL`; `actor` ∈ `AGENT | USER`. Entries are written at the moment of
the event, never reconstructed; one `transaction_id` replays a whole order in
sequence. Exported twice from one record: JSONL for systems
(`exports/TXN-*.jsonl`) and a rendered one-page view for the auditor.

**A category is data, not code.** Each `categories:` block in `config.yaml` holds
that category's cap, default weights, keywords and spec vocabulary.
`agent/config.py` is the ONLY file in `agent/` allowed to name a category —
`tests/test_category_is_data.py` fails if any other module contains one as a live
string. `labels` is configured with no catalog behind it on purpose: it proves
`discovery.available_categories()` reports what we can actually buy (today
furniture, headphones, laptops, packaging) rather than what config holds opinions
about. Furniture is priced to sit inside the authorisation limit (agent proceeds
alone); laptops sit far outside it (agent escalates).

**The four soft criteria are NOT per-category.** Reliability, price, replacement
and delivery are the universal commercial questions — a replacement window is a
warranty on a chair. Only display labels vary. Do not make `SOFT_CRITERIA`
configurable.

**The UI rule — show the decision, not the pipeline.** Banned from the default
surface: stage numbers or names, implementation commentary, file/function/class
names, and any justification of our own design. The reasoning is not deleted — it
is *earned*, through progressive disclosure ("Why this one?", "Show the maths",
"What did we assume?"). Recommendation headline first, comparison table second,
charts where a shape is the point, signal chips not paragraphs, one primary
action per screen, whitespace is fine. Charts are Plotly, never `st.bar_chart`.

---

## 10. Known limits — named on purpose, do not silently "fix" them

- Mock vendors hide real integration pain (no auth, rate limits, pagination,
  downtime). The adapter seam is real; the live adapter is not built.
- Price and stock history are authored demo data, **labelled as such in the UI**.
  We never draw a chart and imply it came from somewhere real.
- No cross-brief optimisation — briefs are priced independently.
- "Negotiation" is a confirm-and-lock exchange, not price bargaining.
- Default weights and market-signal thresholds are our judgement, documented and
  editable in config.
- Relaxation order needs a human signal; with none declared we fall back to
  smallest-violation ordering.
- Spec matching is exact set membership after normalisation — no `>=` comparison.
  "16GB RAM" matches "16gb ram"; "at least 16GB" matches nothing. A miss surfaces
  as a near-miss through the normal escalation path rather than a crash. Do not
  add numeric comparison semantics silently.
- The offline parser recognises a category by keyword. Outside that vocabulary it
  keeps the user's own words, so the brief reaches stage 3 and is declined by name
  against what we actually stock — it does not guess, and it does not ask a
  question the user cannot answer.

---

## 11. Efficiency claims we are allowed to make

The rule: **state only what we have measured.** The catalog is 57 products, the
largest category 17 — big-O theatre at that size costs more than it wins. What is
true, measurable, and recorded in `EFFICIENCY.md` with file paths:

- Two LLM calls per brief (scope, then extraction) — never a chain.
- Zero LLM calls in the decision path — re-ranking is free and identical every run.
- Min/max computed once per criterion, not per product.
- Approval resumes at stage 6 — discovery and the LLM never re-run.
- Append-only audit, O(1) per event, no state reconstruction.
- Config and catalogs parsed once, cached.
- Adding a vendor touches one file; adding a category touches config only.
- Four "best in pool" badges from one pass, not four sorts.
- The market signal is *structurally* unable to change the decision.

---

## 12. The documents, and who each is for

| File | Audience | Contains |
|---|---|---|
| `CLAUDE.md` | any agent working here | the binding design rules — read fully before changing code |
| `CONTEXT.md` (this file) | any chatbot given the repo | the situation report: what exists and what state it is in |
| `PROGRESS.md` | us | the narrative — what we built, in what order, and what broke |
| `EFFICIENCY.md` | judges | engineering claims: claim, `path/file.py:line`, naive alternative, measured number |
| `presentation.txt` | judges | domain judgement: the trap, what the obvious build does, what we do, THE LINE to say out loud |
| `README.md` | anyone | how to run it and what it does |
| `docs/demo-runbook.html` | us, on stage | the run-of-show |

---

## 13. Working agreement (how the humans want to be helped)

The team are **complete beginners on a hackathon clock**. So:

- **One file at a time**, explained in plain terms afterwards so they can say it
  back and defend it to judges.
- Prefer the simplest approach that satisfies the rules. Simple does not mean
  unstructured — it means no abstraction that doesn't pay for itself in this
  build.
- Plain terms, not jargon. Flag time-expensive suggestions explicitly and offer
  the minimal-time alternative first.
- Commit after every working state; push meaningful work to `origin/master`
  without waiting to be asked.
- Log efficiency wins to `EFFICIENCY.md` and pain points to `presentation.txt`
  *as they happen*, with file paths.
- Ask "what breaks in this?" before adding the next feature.
- **No refactors of working code once the demo path is called frozen.**
