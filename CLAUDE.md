# CLAUDE.md — Autonomous Commerce Engineering Agent (Team E-Vets)

Read this file fully before writing or changing any code. Every design rule
here comes from our hackathon submission and is non-negotiable unless we say so
in chat. When a request conflicts with a rule below, stop and flag it.

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

---

## THE ONE RULE EVERYTHING ELSE SERVES

**The LLM interprets language. It never decides the purchase.**

- The LLM reads the imprecise human sentence and turns it into structured data.
  That is the only thing it does.
- Every number a judge could question — score, margin, limit check, ranking —
  is computed in plain Python from the same inputs every time. Same brief in,
  same ranking out, on every run.
- Authorisation limits live in a **config file, not in a prompt.** No phrasing
  inside a user's brief can talk the agent past them, because the agent never
  sees the limit as instruction.

If you ever find yourself asking the LLM to rank, score, or approve — stop.
That is a bug, not a feature.

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
- **Stage 3 — Vendor discovery & filter.** Two mock sources with different
  schemas, normalised into one Product model. Hard-constraint gate: pass/fail.
- **Stage 4 — Ranking.** Weighted score over soft + margin-derived criteria.
  Pure Python.
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

---

## Authorisation (Stage 5) — two limits, not one

- **Per-unit cap (₹22)** — a HARD constraint, enforced by the filter at stage 3.
  No product above it is ever considered.
- **Authorisation limit (₹1,05,000)** — total spend the agent may commit without
  a human, enforced at stage 5. Exceeding it **escalates**, it does not reject.

The autonomous path: passes all hard gates + in stock + order total within the
authorisation limit → proceeds through confirmation, payment, audit close. User
gets a notification after the fact with the ranked comparison and score breakdown.

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
  "event_type": "ESCALATION",   // DECISION | ASSUMPTION | ESCALATION | FALLBACK | ACTION
  "actor": "AGENT",             // AGENT | USER
  "detail": { },
  "reasoning": "one sentence, plain words, not a stack trace",
  "notify": ["requester", "finance"]
}
```

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
- **Discovery:** two mock catalogs with deliberately DIFFERENT schemas — one
  direct JSON, one aggregator CSV — normalised into one `Product` model via
  Pydantic. Different shapes force the normaliser to do real work.
- **Mock services (confirmation, payment):** plain Python modules with an
  injectable-failure flag (out-of-stock, price drift, payment decline). NOT
  separate network services — a module boundary, not a network boundary, so
  swapping in HTTP later is a one-file change. Do not stand up FastAPI/servers
  for the demo.
- **Interface:** Streamlit — chat intake, comparison table, approval screen,
  audit view. Four screens in one app.
- **Config:** a single `config.yaml` holding the authorisation limit, per-unit
  cap defaults, category default weights, and the 5-point substitution
  threshold. Limits are read from here, never hardcoded in logic and never in a
  prompt.

Nothing in ranking or the limit check is generated by an LLM. Determinism is a
feature here, not a limitation.

---

## Demo path (this is the whole deliverable — build only this first)

One unbroken run:
1. An off-topic question is refused, scope stated.
2. A real brief is parsed into hard/soft/ambiguous fields.
3. Two vendor sources with different schemas are normalised.
4. Ranked comparison shown, each score term visible.
5. Order exceeds the authorisation limit → approval screen.
6. User approves → vendor confirmation re-validates price.
7. Payment declines once → retry → confirmed.
8. Audit log exported and read back as the finance view.

Everything outside this path (three-briefs-at-once, cross-brief bundle
discounts, 24h real expiry, real vendor APIs) is "designed, not demoed." Do not
build it unless the demo path is finished, tested, and frozen.

---

## Known limits (we name these on purpose — do not silently "fix" them)

- Mock vendors hide real integration pain (no auth, rate limits, pagination,
  downtime).
- No cross-brief optimisation — briefs priced independently.
- "Negotiation" is a confirm-and-lock exchange, not price bargaining.
- Default weights are our judgement, documented and editable in config.
- Relaxation order needs a human signal; with none declared we fall back to
  smallest-violation ordering.

---

## How to work with us in Claude Code

- **One file at a time.** After each file, explain in plain terms what it does
  so we can say it back and defend it to judges.
- **Match the golden numbers.** Any change touching the ranker must keep the
  58.0 / 48.7 / 33.7 test green.
- **Commit after every working state.**
- **Ask "what breaks in this?"** before adding the next feature.
- **No refactors of working code once we say the demo path is frozen.**
- If a request would put a decision (ranking, scoring, limit checks) inside the
  LLM, stop and flag it — that violates the one rule.
