# Autonomous Commerce Engineering Agent

**Team E-Vets**

A plain-language buying brief goes in. An audited, human-approved procurement
decision comes out — with the agent knowing exactly where its own authority ends.

> **"5,000 kraft mailer boxes, double-wall, 200×150×80 mm, max ₹22 per unit,
> delivered within 10 days. Reliability matters a lot — we got burned last
> quarter."**

That sentence is the entire input. What comes back is a ranked comparison, the
arithmetic behind it, a timing read on each option, and — because this order is
over the limit the agent may commit alone — a request for a human to approve it.

**Tagline: autonomy the user can audit.**

---

## Run it

```bash
python -m pip install -r requirements.txt
streamlit run app.py
```

That is the whole setup. An API key is optional:

```bash
cp .env.example .env          # then paste a Gemini key from aistudio.google.com
```

With no key, the app reads briefs with a deterministic word-matching parser and
says so on screen. Nothing else changes — the other eight stages never touch a
language model.

Run the tests with `python -m pytest -q` (241 of them, no network, ~50 s).

---

## The one rule everything else serves

**The LLM interprets language. It never decides the purchase.**

- The model reads the imprecise human sentence and turns it into structured
  fields. That is the only thing it does.
- Every number a judge could question — score, margin, limit check, ranking,
  market signal — is computed in plain Python from the same inputs every time.
  Same brief in, same ranking out, on every run.
- Authorisation limits live in `config.yaml`, **not in a prompt**. No phrasing
  inside a brief can talk the agent past them, because the agent never sees the
  limit as instruction.

---

## What it does, in order

| | Stage | What happens |
|---|---|---|
| 0 | Scope gate | Off-topic input is declined. An incomplete brief gets **one** targeted question — no discovery starts on a guess |
| 1 | Requirement extraction | The sentence becomes structured fields, each tagged `hard` / `soft` / `ambiguous` |
| 2 | Weight engine | Stated priorities become weights summing to 1. The model finds the priority; Python computes the weights |
| 3 | Discovery & filter | Six sources, three different schemas, one normalised `Product`. Hard constraints are pass/fail |
| 4 | Ranking | Weighted score over the soft criteria. ~60 lines of Python, unit-tested against a frozen table |
| 4.5 | Market signal | Stock cover, price trend, urgency — **advisory only**, and structurally unable to change anything |
| 5 | Authorisation | Proceeds alone, or escalates on three defined triggers. The only human-in-the-loop gate |
| 6 | Vendor confirmation | Re-validates price and stock before any money moves |
| 7 | Payment | Simulated. Retries once, then falls back |
| 8 | Audit close | Final entry written; requester and finance notified |

A shared transaction context and an append-only audit log run alongside every
stage. No state is reconstructed after the fact.

---

## The ranking, in full

Scoring runs only over products that already passed the hard gates.

```
score = Σ ( weight × normalised )   over soft criteria only
```

For the brief above — reliability 0.45 (the buyer said so), price 0.20,
replacement 0.20, delivery 0.15:

| Product · source | ₹/unit | Days | Reliab. | Replace | Score |
| --- | --- | --- | --- | --- | --- |
| Corusafe DW · PackHub (direct) | 21.90 | 4 | 4.8 | 7 d | **58.0** |
| KraftPro DW · PackHub (direct) | 20.90 | 6 | 4.6 | 10 d | 48.7 |
| EcoMail DW · BoxBazaar (aggregator) | 17.60 | 9 | 4.1 | 30 d | 33.7 |

The winner's arithmetic: `0.45×1.000 + 0.20×0.067 + 0.15×0.775 + 0.20×0.000 =
0.580 → 58.0`.

**The cheapest option ranks last.** EcoMail is 20% under the price cap and comes
third, because the buyer said reliability matters. The formula has no favourite;
the brief does. Reliability contributed 0.450 of Corusafe's 0.580 and 0.000 of
EcoMail's 0.337.

These three numbers are frozen in `tests/test_ranking.py`. If the code ever
disagrees with them, the code is wrong.

---

## Where the agent stops

Two limits, doing two different jobs:

- **Per-unit cap (₹22)** — a hard constraint. Enforced at stage 3. Nothing above
  it is ever considered.
- **Authorisation limit (₹1,05,000)** — total spend the agent may commit without
  a human. Enforced at stage 5. Exceeding it **escalates**; it does not reject.

Three escalation triggers, and nothing else: no product passes the hard gates;
the order total exceeds the authorisation limit; or the vendor or the payment
fails at execution.

Deliberately **not** triggers: the top pick being imperfect on a soft preference
(ranking absorbs that), and a market signal saying "buy now".

> A market signal can never alter eligibility, score, ranking, or the
> authorisation decision. "Order today" makes a human decide *sooner*. It never
> makes the agent decide *alone*.

An agent that can talk itself past its own limit by claiming urgency is exactly
the failure this project exists to rule out.

**Silence is never approval.** A pending request expires rather than proceeding.
State is preserved, so approving later resumes at stage 6 — discovery and the
language model never re-run.

---

## The audit trail

Every event, written the moment it happens, in one schema:

```json
{
  "entry_id": "TXN-4471-07",
  "transaction_id": "TXN-4471",
  "timestamp": "2026-08-21T10:22:41+05:30",
  "stage": "5 - decision & authorisation",
  "event_type": "ESCALATION",
  "actor": "AGENT",
  "detail": { },
  "reasoning": "one sentence, plain words",
  "notify": ["requester", "finance"]
}
```

It answers a finance manager's four questions: what happened, why, what the
agent did about it, and who needs to know. One transaction id replays the whole
order in sequence. Exported twice from one record — JSONL for systems, a
one-page rendered view for the auditor.

Whoever acts is recorded as `User` or `Agent`. We do not know who is signed in,
and inventing a name in a record a finance manager reads is exactly the kind of
unearned confidence this project exists to rule out.

---

## Adding a source is one file

```
SourceAdapter.fetch() -> list[Product]
```

Six adapters over three schema shapes — direct vendor JSON, aggregator CSV
(paise, ship ranges, scores out of 100, returns written as a sentence), and
marketplace JSON. Each owns its own mess and hands back the same normalised
`Product`. Everything downstream is source-agnostic.

The interface carries a source toggle. Same engine, different feeds, identical
behaviour — we show the abstraction rather than claiming it.

**A category is data, not code.** Each entry in `config.yaml` holds its cap,
default weights, keywords and spec vocabulary. `tests/test_category_is_data.py`
fails if any module outside `agent/config.py` names one. We stock packaging,
furniture, laptops and headsets.

---

## What we do not claim

Named on purpose, because a limit we state is worth more than one a judge finds:

- The adapter seam is real; **a live vendor API adapter is not built.** Mock
  vendors hide auth, rate limits, pagination and downtime.
- Price and stock history are **authored demo data**, labelled as such on screen.
  We do not draw a chart and imply it came from somewhere real.
- No cross-brief optimisation — briefs are priced independently.
- "Negotiation" is a confirm-and-lock exchange, not price bargaining.
- Default weights and market-signal thresholds are our judgement — documented
  and editable in config, not discovered from data.
- Spec matching is exact set membership after normalisation. "16GB RAM" matches
  "16gb ram"; "at least 16GB" matches nothing, and surfaces as a near-miss
  rather than a crash.

---

## Reading the repo

| File | What it is for |
| --- | --- |
| `CLAUDE.md` | The design rules. Every constraint above, with its reasoning |
| `PROGRESS.md` | Where the build is, so we can restart cold |
| `EFFICIENCY.md` | The engineering claims — each with a file path and a measured number |
| `presentation.txt` | The domain judgement: traps we hit, what the obvious build does, and what we do instead |

Efficiency claims we can defend, because they were measured on this repo: two
LLM calls per brief and never a chain; **zero** LLM calls in the decision path;
the full decision path in 9.5 ms; approval resumes at stage 6 without re-running
discovery; append-only audit at O(1) per event.

Nothing in ranking, the limit check or the market signal is generated by a
language model. Determinism is the feature.
