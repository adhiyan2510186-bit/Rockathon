# CLAUDE.md — Autonomous Commerce Engineering Agent (Team E-Vets)

Read this file fully before writing or changing any code. Every design rule
here comes from our hackathon submission and is non-negotiable unless we say so
in chat. When a request conflicts with a rule below, stop and flag it.

---

## Team context — read this before suggesting anything

We are complete beginners and we have very minimal time (hackathon
constraints). Practical implications:

- Prefer the simplest approach that satisfies the rules in this file. Simple
  does NOT mean unstructured — see "The product bar" below. It means no
  abstraction that doesn't pay for itself within this build.
- Explain things in plain terms, not jargon-heavy shorthand — we need to be
  able to defend every file to judges.
- Flag time-expensive suggestions explicitly and offer the minimal-time
  alternative first.
- Stay strictly inside the "Demo path" section below — do not suggest
  building anything marked "designed, not demoed."

---

## What we are building

An AI agent that turns a plain-language buying brief into an audited,
human-approved procurement decision. One sentence in ("5,000 kraft mailer
boxes, double-wall, max ₹22/unit, delivered within 10 days, reliability matters
a lot") → a ranked, explained purchase decision out, with the agent knowing
exactly where its own authority ends.

Tagline: **"Autonomy the user can audit."**

Running example throughout: Meena, ops manager at a Chennai D2C skincare brand,
reordering packaging (mailer boxes, labels, void fill) from a few vendors, each
with its own price cap and delivery window.

Meena is our persona — she is who we design *for*, not a name the product prints
back. **No screen ever names her.** Anywhere the UI or the audit trail records
who acted, it says **`User`** (the agent's own side says `Agent`). We do not know
who is signed in, and inventing a name in a record a finance manager reads is
exactly the kind of unearned confidence this project exists to rule out.

---

## THE ONE RULE EVERYTHING ELSE SERVES

**The LLM interprets language. It never decides the purchase.**

- The LLM reads the imprecise human sentence and turns it into structured data.
  That is the only thing it does.
- Every number a judge could question — score, margin, limit check, ranking,
  market signal — is computed in plain Python from the same inputs every time.
  Same brief in, same ranking out, on every run.
- Authorisation limits live in a **config file, not in a prompt.** No phrasing
  inside a user's brief can talk the agent past them, because the agent never
  sees the limit as instruction.

If you ever find yourself asking the LLM to rank, score, approve, or judge
urgency — stop. That is a bug, not a feature.

---

## THE PRODUCT BAR — what "not a demo" means

We are building a **product**, not a walkthrough of our own architecture. The
difference is visible in one place above all others: the UI.

### The UI rule: show the decision, not the pipeline

Meena is an ops manager. She does not know what "Stage 4" is and must never be
shown the word. Every one of these is banned from the default UI surface:

- Stage numbers or stage names ("Stage 5 · decision & authorisation")
- Implementation commentary ("pure Python, same brief in, same order out")
- Justifications of our own design ("kept, with reasons", "so nothing is taken
  on trust", "Two schemas is the point")
- File names, function names, trigger enum values, class names
- Anything whose audience is a judge rather than a buyer

**This does not mean deleting the reasoning.** The reasoning IS our product —
"Autonomy the user can audit" is the whole thesis. It means the reasoning is
**earned, not shoved**:

> **Progressive disclosure.** Clean surface by default. One obvious affordance
> per claim — "Why this one?", "Show the maths", "What did we assume?" — that
> opens the full breakdown underneath.

We then demo the drill-down deliberately. A judge watching us *open* the score
breakdown on request is far more convincing than a judge reading it in a wall
of captions. The explanation moves from the screen into **our mouths and our
code**. If we cannot explain a file out loud, the fix is to understand the file,
not to print a paragraph next to it.

### What the UI must look like

Take the cue from AI agents that present analysis for a living (stock/market
copilots, credit-decision dashboards). Concretely:

- **A decision headline, not a table dump.** The recommendation first, in one
  sentence, with cost and confidence. Comparison table second.
- **Charts over numbers where a shape is the point.** Score composition per
  product (stacked contribution bars), price history, stock burn-down. A judge
  reads a bar chart in one second and a table in fifteen.
- **Signal chips, not paragraphs.** "3 days of stock cover", "price up 4% in
  2 weeks", "fastest delivery in pool" — short, scannable, colour-coded.
- **One primary action per screen.** Approve. Not six buttons of equal weight.
- **Dead space is fine.** Crowding reads as amateur; whitespace reads as shipped.

### Where the explanation goes instead

- Drill-downs and hover states in the UI (opened on demand)
- `PROGRESS.md` — the narrative for us
- `EFFICIENCY.md` — the engineering claims (speed, cost, complexity), with paths
- `presentation.txt` — the domain judgement: pain points, traps, and the exact
  lines to say out loud. What EFFICIENCY.md is to *how well we built it*,
  presentation.txt is to *how well we understood the problem*.
- Module docstrings — for us and for judges reading the repo

---

## Architecture: stages 0 → 8

Data flows 0 → 8. A shared **Transaction Context** and an append-only **Audit
Logger** run alongside every stage — no state is reconstructed after the fact.

- **Stage 0 — Scope & completeness gate.** Off-topic input is declined (states
  its scope, stays ready for the next message). An incomplete purchasing brief
  gets ONE targeted question — no discovery starts on a guess.
- **Stage 1 — Requirement extraction.** LLM → JSON. Each field tagged
  `hard` / `soft` / `ambiguous`.
- **Stage 2 — Preference & weight engine.** Stated priorities → weights that sum
  to 1. Category defaults (held in config) when a priority is unstated. LLM
  extracts the priority; Python computes the weights.
- **Stage 3 — Vendor discovery & filter.** Sources arrive through the **source
  adapter layer** (see below), normalised into one Product model.
  Hard-constraint gate: pass/fail.
- **Stage 4 — Ranking.** Weighted score over soft + margin-derived criteria.
  Pure Python.
- **Stage 4.5 — Market signal.** Deterministic timing/urgency read over each
  product's price and stock history. Advisory only. Pure Python.
- **Stage 5 — Decision & authorisation.** Proceeds alone, OR escalates on 3
  defined triggers. This is the only human-in-the-loop gate.
- **Stage 6 — Vendor confirmation & lock.** Re-validate price and stock before
  any execution.
- **Stage 7 — Mock payment execution.** Simulated outcome; retry once, then fall
  back.
- **Stage 8 — Confirmation & audit close.** Final structured entry; user and
  finance notified.

---

## The three constraint classes (Stage 1 tagging)

- **HARD** — pass/fail, controls eligibility, never relaxed if non-negotiable.
  Category, quantity, specs, per-unit cap, delivery window. The non-negotiable
  tier (category, quantity, safety spec) is never relaxed. The negotiable tier
  (price, delivery, minor spec gap) may be surfaced as a near-miss — proposed,
  never applied silently.
- **SOFT** — ranking only, never rejects. Seller reliability, replacement
  window, packaging quality. Weight from the user's own words if stated, else
  category default (documented in config, not invented at runtime).
- **AMBIGUOUS** — any field the parser cannot extract confidently. Asked once
  before discovery. If unanswered: a declared default is applied and logged as
  `ASSUMED`, never as `CONFIRMED`.

**Classification controls eligibility, not scoring.** The same attribute can
appear in both roles — price is a hard gate at stage 3, then a margin-derived
soft criterion at stage 4. Two different questions ("does it qualify?" vs "how
much do we prefer it among qualifiers?"). Reuse of one measurement, not double
counting. Label it that way in code and comments.

---

## Ranking formula (Stage 4) — must reproduce these exact numbers

```
score = Σ ( weight × normalised )   over soft criteria only
```

- Hard gates have already filtered the pool, so scoring runs over survivors only.
- `weight` = user-stated if given, else category default.
- Margins use `√margin`, so a bargain cannot drown out what the user said
  mattered.

**Golden test — the ranker MUST produce this table for the demo brief.** Write a
unit test that asserts these numbers. If the code disagrees with the deck, the
code is wrong.

Brief: "5,000 kraft mailer boxes, double-wall, 200×150×80 mm, max ₹22 per unit,
delivered within 10 days. Reliability matters a lot — we got burned last quarter."

Weights: reliability 0.45 (user-stated), price 0.20, replacement 0.20, delivery 0.15.

| Product · source                   | ₹/unit | Days | Reliab. | Replace | Score |
| ---------------------------------- | ------ | ---- | ------- | ------- | ----- |
| Corusafe DW · PackHub (direct)     | 21.90  | 4    | 4.8     | 7 d     | 58.0  |
| KraftPro DW · PackHub (direct)     | 20.90  | 6    | 4.6     | 10 d    | 48.7  |
| EcoMail DW · BoxBazaar (aggregator)| 17.60  | 9    | 4.1     | 30 d    | 33.7  |

Winner's arithmetic (Corusafe): `0.45×1.000 + 0.20×0.067 + 0.15×0.775 +
0.20×0.000 = 0.580 → 58.0`.

Reliability contributed 0.450 of Corusafe's 0.580 and 0.000 of EcoMail's 0.337.
The cheapest option (EcoMail, 20% under cap) ranks LAST because the user said
reliability matters. The formula has no favourite; the brief does.

**This table is frozen.** Stage 4.5 (below) must not change a single one of
these numbers.

---

## Stage 4.5 — Market signal (timing, not authority)

The ranker answers *"which product fits the brief best?"* It does not answer
*"is now a good time to buy it?"* Stage 4.5 answers the second question, and
only ever as advice.

### Where the data comes from — declare it, never fake it

Every source adapter supplies, per product, alongside price and stock:

- `price_history` — dated per-unit price points
- `stock_history` — dated stock-level points

In the synthetic catalogs these series are **authored demo data, and the UI says
so** ("simulated market data"). We do not draw a chart and imply it came from
somewhere real. A judge asking "where is this from?" must get a clean answer.
Fabricating provenance would contradict the exact property we are selling.

### The signals — computed in Python, from the series, every time

| Signal | Computed from | Example surface text |
| ------ | ------------- | -------------------- |
| Stock cover | recent depletion rate vs current stock vs order qty | "3 days of cover at current rate" |
| Price trend | slope over the window | "up 4% in 2 weeks" |
| Buy-now urgency | stock cover < lead time, or rising trend | "Order today — stock runs out before delivery" |
| Pool position | rank of this product's price/delivery among survivors | "fastest delivery in pool" |

Thresholds live in `config.yaml` next to every other limit. Never hardcoded,
never in a prompt, never produced by the LLM.

### THE GUARDRAIL — urgency changes priority, never authority

**A market signal must never alter eligibility, score, ranking, or the
authorisation decision.** It cannot:

- move a product past a hard gate
- add or subtract a single point of score
- reorder the ranking
- justify committing spend above the authorisation limit
- convert an escalation into an auto-proceed

"Buy now" makes a human decide *sooner*. It never makes the agent decide
*alone*. An agent that can talk itself past its own limit by claiming urgency
is precisely the failure mode this whole project exists to rule out — and it is
the first thing a sharp judge will probe. Every signal is logged as its own
audit event so the record shows it was advisory.

---

## Source adapter layer (Stage 3)

Discovery must not know where products come from. One interface, many sources:

```
SourceAdapter.fetch() -> list[Product]
```

Each adapter owns its own mess — its schema, its units, its missing fields —
and hands back the same normalised `Product`. Everything downstream (filter,
ranker, signal, authorisation, audit) is source-agnostic and untouched when a
source is added.

Current adapters — six, over three shapes:

- `PackHubAdapter`, `OfficeStockAdapter` — direct vendor JSON (rupees, days,
  rating /5, clean specs)
- `BoxBazaarAdapter`, `TradeBridgeAdapter` — aggregator CSV (paise, ship ranges,
  score /100, returns as a sentence, specs as a pipe-blob)
- `AmazonAdapter`, `FlipkartAdapter` — marketplace JSON (nested rating object,
  its own delivery wording)

Six adapters, three `read()` implementations. That ratio is the point: a new
source of a shape we already handle is a subclass and a file path.

The UI carries a **source toggle**. Same engine, different feeds, identical
downstream behaviour. That toggle is the demonstration: we are not claiming the
abstraction, we are showing it.

**Adding a live vendor API is one new adapter file and nothing else.** That is
the claim the layer earns us. We are not building one for this demo (see
"designed, not demoed") — the point is that the seam exists and is real.

---

## Authorisation (Stage 5) — two limits, not one

- **Per-unit cap (₹22)** — a HARD constraint, enforced by the filter at stage 3.
  No product above it is ever considered.
- **Authorisation limit (₹1,05,000)** — total spend the agent may commit without
  a human, enforced at stage 5. Exceeding it **escalates**, it does not reject.

The autonomous path: passes all hard gates + in stock + order total within the
authorisation limit → proceeds through confirmation, payment, audit close. User
gets a notification after the fact with the ranked comparison and score breakdown.

**Who approved is recorded as `User`, never a persona name** (`app.py`,
`APPROVER`). The stage-5 entry is one of the few written with actor `USER` — it
marks where the agent's authority ended and a person's began. What matters in
that record is *that a human stepped in*, not a name we would be making up.

Three escalation triggers, and nothing else:
1. No product passes all hard gates → relax negotiable constraints only, in the
   user's declared flexibility order; surface 2–3 near-misses with deltas.
2. Valid match but order total over the authorisation limit → no purchase, state
   held; show overage + why still recommended + best in-limit alternative.
3. Unavailable at confirmation OR payment declined → auto-fall-back to next
   eligible product, independently re-validated; escalate if score gap > 5 pts
   or the fallback also fails.

**Deliberately NOT a trigger:** the top pick being imperfect on a soft
preference. Ranking absorbs that. Escalate on it and every order escalates,
which erases the boundary we are demonstrating.

**Also deliberately NOT a trigger, and not an override:** a market signal.
See the Stage 4.5 guardrail.

**Silence is never approval.** A pending request expires rather than proceeding.
Transaction state is preserved, so approving later resumes at stage 6, it does
not re-run discovery.

**Substitution threshold = 5 points.** If #1 leads #2 by more than 5 points and
#1 becomes unavailable, do NOT silently swap — escalate, because a wide gap
means #2 is a meaningfully worse fit. (In the demo, #1 leads #2 by 9.3 points.)

---

## Escalation & fallback — ONE handler, not four patches

One mechanism, entered from four places (stage 3 no eligible match, stage 5 over
limit, stage 6 unavailable/price drift, stage 7 payment failure ×2):

1. **DETECT** — which invariant broke, at which stage.
2. **RE-VALIDATE** — is the next option independently eligible? Never trust sort
   order.
3. **RELAX** — negotiable constraints only, in the user's declared flexibility
   order.
4. **RE-RANK & SURFACE** — 2–3 options with explicit violation deltas.
5. **ESCALATE & LOG** — one audit entry, state preserved, no silent action.

Build this as one shared component invoked from four call sites. Not four
separate code paths.

---

## Audit trail (Stage 8) — one schema, written the moment it happens

Every entry uses the same wrapper:

```json
{
  "entry_id": "TXN-4471-07",
  "transaction_id": "TXN-4471",
  "timestamp": "2026-08-14T10:22:41+05:30",
  "stage": "5 · decision & authorisation",
  "event_type": "ESCALATION",
  "actor": "AGENT",
  "detail": { },
  "reasoning": "one sentence, plain words, not a stack trace",
  "notify": ["requester", "finance"]
}
```

`event_type` is one of: `DECISION` | `ASSUMPTION` | `ESCALATION` | `FALLBACK` |
`ACTION` | `MARKET_SIGNAL`. `actor` is `AGENT` or `USER`.

`MARKET_SIGNAL` is advisory by definition: an entry of this type never
accompanies a change in eligibility, score, or authorisation.

The log answers a finance manager's four questions: WHAT happened (event_type in
plain words), WHY (one sentence of reasoning), WHAT the agent did about it (the
action, or "no purchase executed"), WHO needs to know (the notify list drives an
automatic finance email).

Three properties to hold:
- Written at the moment of the event — an assumption logged after the purchase
  is just a story.
- One `transaction_id` replays the whole order in sequence without reading code.
- Exported twice from one record: JSONL for systems, a one-page rendered view
  for the auditor.

---

## Tech stack & tooling decisions

- **Language step (stages 0–2):** LLM API with schema-constrained JSON output.
  Only the language step touches a language model.
- **Everything else:** plain Python. Ranking is ~60 lines, unit-tested.
- **Discovery:** source adapters behind one interface. The two current catalogs
  have deliberately DIFFERENT schemas — one direct JSON, one aggregator CSV —
  normalised into one `Product` model via Pydantic. Different shapes force the
  normaliser to do real work.
- **Mock services (confirmation, payment):** plain Python modules with an
  injectable-failure flag (out-of-stock, price drift, payment decline). NOT
  separate network services — a module boundary, not a network boundary, so
  swapping in HTTP later is a one-file change. Do not stand up FastAPI/servers
  for the demo.
- **Interface:** Streamlit. Charts via Plotly (interactive, hover, reads as a
  product) — not `st.bar_chart`, which reads as a notebook.
- **Config:** a single `config.yaml` holding the authorisation limit, the
  5-point substitution threshold, the market-signal thresholds, and one
  `categories:` block. Limits are read from here, never hardcoded in logic and
  never in a prompt.
- **A category is data, not code.** Each entry in the `categories:` block holds
  that category's per-unit cap default, default weights, trigger keywords, spec
  vocabulary and trade shorthand. `agent/config.py` is the only file in `agent/`
  permitted to name a category; `tests/test_category_is_data.py` fails if any
  other module contains one as a live string. Adding a category is a config
  block plus catalog rows — no new branch, no new code path. We currently stock
  **packaging, furniture, laptops and headsets**, priced so that furniture sits
  inside the authorisation limit (agent proceeds alone) and laptops sit far
  outside it (agent escalates). `labels` is configured with no catalog behind it
  on purpose — it proves `available_categories()` reads what we can actually
  buy rather than what we hold opinions about.
- **The four soft criteria are NOT per-category.** Reliability, price,
  replacement and delivery are the universal commercial questions — a
  replacement window is a warranty on a chair. Only display labels may vary.
  Do not make `SOFT_CRITERIA` configurable; it is a cross-cutting change through
  the ranker, the weight engine, every weight table and the chart palette.

Nothing in ranking, the limit check, or the market signal is generated by an
LLM. Determinism is a feature here, not a limitation.

---

## Efficiency — measured, never claimed

We want to show judges this system is efficient. The rule is:

> **State only what we have measured. Never state a complexity claim that the
> data size does not justify.**

Our catalog is 57 products across six sources — the largest single category is
17. Big-O theatre at that size is worse than silence: one judge asking *"why
does O(n log n) matter here?"* costs us more than the claim ever won. What we
may legitimately claim, because it is true and measurable:

| Claim | Why it is real |
| ----- | -------------- |
| **Two LLM calls per brief** (scope, then extraction) — never a chain | Fewer round trips, lower cost, lower latency. Both happen before any decision; neither is repeated |
| **Zero LLM calls in the decision path** | Re-ranking is free, instant, and identical every run |
| **Min/max computed once per criterion**, not per product | Genuine O(n·k) instead of O(n²·k) |
| **Approval resumes at stage 6** | Discovery and the LLM never re-run on approval |
| **Append-only audit, O(1) per event** | No state reconstruction, no replay cost |
| **Config and catalogs parsed once, cached** | Repeat interactions touch no disk |

### `EFFICIENCY.md` — the presentation file

Maintain `EFFICIENCY.md` at the repo root. It is the source for our efficiency
talking points. Every entry must carry:

1. **The claim** in one plain sentence
2. **The exact location** — `path/to/file.py:line`, function name
3. **What the naive version would have done**, and why ours is better
4. **A measured number** where one exists (ms, token count, call count),
   with how it was measured

Add to it as we build, not at the end. An entry without a file path is useless
on stage — we must be able to open the code the moment a judge asks.

---

## Demo path (this is the whole deliverable — build only this first)

One unbroken run:
1. An off-topic question is refused, scope stated.
2. A real brief is parsed into hard/soft/ambiguous fields.
3. Two vendor sources with different schemas are normalised — with the source
   toggle shown working.
4. Ranked comparison shown as a decision, with the score breakdown available on
   demand rather than printed by default.
5. Market signals surfaced alongside the recommendation, clearly advisory.
6. Order exceeds the authorisation limit → approval screen.
7. User approves → vendor confirmation re-validates price.
8. Payment declines once → retry → confirmed.
9. Audit log exported and read back as the finance view.

Everything outside this path is "designed, not demoed":

- **Live vendor API adapter** (the seam exists; no implementation this round)
- Three-briefs-at-once, cross-brief bundle discounts
- 24h real expiry
- Real payment rails

Do not build these unless the demo path is finished, tested, and frozen.

---

## Known limits (we name these on purpose — do not silently "fix" them)

- Mock vendors hide real integration pain (no auth, rate limits, pagination,
  downtime). The adapter seam is real; the live adapter is not built.
- Price and stock history are authored demo data, labelled as such in the UI.
- No cross-brief optimisation — briefs priced independently.
- "Negotiation" is a confirm-and-lock exchange, not price bargaining.
- Default weights and market-signal thresholds are our judgement, documented and
  editable in config.
- Relaxation order needs a human signal; with none declared we fall back to
  smallest-violation ordering.
- Spec matching is exact set membership after normalisation — no `>=`
  comparison. "16GB RAM" matches "16gb ram"; "at least 16GB" does not match
  anything. A miss surfaces as a near-miss through the normal escalation path
  rather than a crash, and catalog specs are authored in the same vocabulary the
  config lists. Do not silently add numeric comparison semantics.
- The offline parser recognises a category by keyword. For anything outside that
  vocabulary it keeps the user's own words, so the brief reaches stage 3 and is
  declined by name against what we actually stock — it does not guess, and it
  does not ask a question the user cannot answer.

---

## How to work with us in Claude Code

- **One file at a time.** After each file, explain in plain terms what it does
  so we can say it back and defend it to judges.
- **Match the golden numbers.** Any change touching the ranker must keep the
  58.0 / 48.7 / 33.7 test green. Stage 4.5 must not move them by a hair.
- **Product bar on every UI change.** Before adding text to a screen, ask: would
  Meena read this, or is it aimed at a judge? If the latter, it belongs in a
  drill-down, a docstring, or `EFFICIENCY.md`.
- **Log efficiency wins as they happen.** Any genuinely efficient piece of code
  gets an `EFFICIENCY.md` entry with its file path, at the time it is written.
- **Log pain points as they happen.** Whenever we make a call a reasonable
  engineer could have made differently and we had a reason — a trap in the
  domain, a bug we hit, a tempting shortcut we refused — add it to
  `presentation.txt` in the same shape: the trap, what the obvious build does,
  what we do, and THE LINE to say. That file is the evidence we understood the
  problem, not just the code. Skip anything that is only a feature description.
- **Commit after every working state.**
- **Auto-push meaningful work.** Once a file or change is working state — not a
  half-finished edit — commit and push it to the GitHub remote (origin/master)
  without waiting to be asked each time. "Meaningful" means it runs, passes
  its own checks (e.g. the golden ranker test if touched), and is something we
  could show a judge. Scratch experiments and broken intermediate states stay
  local until they clear that bar.
- **`v1-demo-frozen` is the safety net.** That tag is a known-good, runnable
  demo. Never delete it; if a refactor goes wrong we present from there.
- **Ask "what breaks in this?"** before adding the next feature.
- **No refactors of working code once we say the demo path is frozen.**
- If a request would put a decision (ranking, scoring, limit checks, urgency)
  inside the LLM, stop and flag it — that violates the one rule.
