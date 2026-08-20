# PROGRESS.md — where we are, so we can restart cold

Read CLAUDE.md first (the rules). Read this second (the state).
Last updated: 2026-08-20.

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
| 12 | `agent/authorisation.py` | 5 — the limit check | **DONE** |
| 13 | `agent/vendor.py` | 6 — confirmation & lock, with failure switches | **DONE** |
| 14 | `agent/payment.py` | 7 — mock payment, retry once then fall back | **DONE** |
| 15 | `agent/close.py` | 8 — the final entry, the only COMPLETED | **DONE** |
| 16 | `app.py` | Streamlit, four screens | **DONE — demo path runs end to end** |

**Every file in the demo path is built.** The eight-step run in CLAUDE.md goes
end to end: `python -m streamlit run app.py`. What follows is one section per
file, in build order, so a cold restart can pick up anywhere.

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

### Stage 5: the limit check (`agent/authorisation.py`)

The only human-in-the-loop gate, and deliberately the smallest file so far. One
multiplication and one comparison:

```
order total = unit price x quantity        5,000 x Rs 21.90 = Rs 1,09,500
within  the Rs 1,05,000 limit -> the agent buys it alone, tells the user after
over    the Rs 1,05,000 limit -> escalation, nothing bought, a human decides
```

`authorise(context, audit)` is the whole stage. It takes #1 from the ranked list
(it never re-ranks — stage 4 already chose), records it as `context.selected` so
a later approval has something to resume with, and compares the total to the
limit read from config.yaml.

**Two limits, and they do different jobs.** The Rs 22 per-unit cap is a
constraint on the PRODUCT (stage 3 removes anything above it). The Rs 1,05,000
authorisation limit is a constraint on the AGENT — an over-limit order is
perfectly eligible, the agent just may not sign for it. So this file never
removes anything from the ranked list; it only decides who signs.

**Over the limit, it hands straight to `escalation.handle()`** with trigger #2
rather than writing its own approval screen. Trigger #2 is a call site, not a
code path — the overage sentence a judge reads comes from the same place whether
the trouble was found at stage 3, 5, 6 or 7.

**The autonomous path is logged as loudly as the escalation.** The within-limit
DECISION entry records total, limit and headroom at the moment the agent decided
it did not need to ask, so "why was I not consulted?" is answered by a line that
already existed.

Three ways out of `AWAITING_APPROVAL`, and two of them buy nothing:

| Function | Actor logged | Result |
|---|---|---|
| `approve(ctx, audit, approver=...)` | USER | status APPROVED, resumes at stage 6 |
| `decline(ctx, audit, reason=...)` | USER | status DECLINED, no purchase, reason kept verbatim |
| `expire(ctx, audit)` | AGENT | status EXPIRED, no purchase, state preserved |

`expire()` is what makes "silence is never approval" a mechanism instead of a
claim, and the actor is AGENT because the user did nothing — that is the fact
being recorded. `approve()` does no re-discovery and no re-ranking: the list the
user was shown is the list they approved. Stage 6 re-validates price and stock at
the counter instead.

Verified on the demo brief: escalates at Rs 1,09,500 vs Rs 1,05,000 (Rs 4,500
over, headroom -4,500, gap 9.3, KraftPro offered at Rs 1,04,500); the same brief
at 4,000 units proceeds alone at Rs 87,600 with Rs 17,400 of headroom. Approving,
declining and expiring each land in the right state, and calling any of the three
on a transaction that is not awaiting approval raises rather than silently
acting. Golden test still 14 green.

### Stage 6: confirmation & lock (`agent/vendor.py`)

The last check before money moves. Stages 3-5 all worked from a snapshot taken
at the start of the run, and a human may have spent an hour deciding in the
middle of it. So we go back to the vendor and ask two questions about the exact
product we are about to buy: **do you still have 5,000, and is it still Rs
21.90?**

`confirm(context, audit, overrides=None)` is the whole stage. It refuses to run
unless the transaction is APPROVED — reaching a vendor counter without passing
stage 5 would mean the one human-in-the-loop gate had been skipped.

**It re-reads the catalog rather than trusting `context.selected`.**
`_live_record()` goes back through `discovery.discover()`, a genuine second
lookup. Checking our stage-3 copy against itself would pass every time and prove
nothing. In a real build that function is the vendor's API call — swapping it is
a one-function change, which is the whole reason it is a module and not a server.

**Re-validation reuses `discovery.apply_hard_gates`.** We rebuild the product at
the vendor's quoted figures and put it through the same gate stage 3 used. Same
reason as in escalation.py: two definitions of "eligible" in one codebase is how
an ineligible product ends up bought.

Three things can go wrong, and all three leave through `escalation.handle()`
with trigger #3 — this file writes no approval screen of its own:

| What moved | What the human reads |
|---|---|
| stock | "is out of stock at confirmation - has 0 in stock against 5,000 needed" |
| price up | "no longer passes a hard constraint - Rs 23.65 exceeds the Rs 22.00 cap" |
| money | "would cost Rs 1,09,750, above the Rs 1,09,500 authorised for this order" |

**The agent may lock only two things**: an order at exactly the price that was
approved, or one that got CHEAPER (nobody needs approval to spend less). Any
increase escalates, however small — Rs 21.95 is still not the number anybody
said yes to.

**The money ceiling is a hole we found while testing, not a feature we planned.**
The escalation handler gates a trigger-3 fallback on the SCORE gap — how
different the box is — never on what it costs. With our catalog the fallback is
always cheaper, so it never bites; but "the agent may not commit more than it was
authorised to" cannot be a rule that only holds because seven mock products
happen to be priced conveniently. `_authorised_ceiling()` is `max(the agent's
limit, the total a human actually approved)`, so both routes into stage 6 carry
their own ceiling and neither number is invented here.

**A fallback is confirmed at the counter too.** When the handler resolves a swap,
`confirm()` loops and quotes the replacement as well, rather than assuming a swap
made on paper is one the vendor will honour. It terminates because every product
tried is added to `exclude_ids` and never offered back.

Two smaller decisions worth defending:

- **The failure switches apply to the FIRST quote only.** They describe one
  vendor having one bad day. A flag that broke every vendor in the country would
  make the fallback path untestable, and that is the path we most want to show.
  Each quote records which switches were on, so an injected demo failure can
  never be read back later as something a vendor really did.
- **The lock reference is derived, not random** — `LOCK-TXN-4471-PH-CORUSAFE-DW`.
  Same run, same reference, every rehearsal. It is quoted at payment and in the
  audit entry, so the thing paid for and the thing confirmed are provably the
  same thing.

Verified on the demo brief: clean confirmation locks Corusafe at Rs 21.90 x 5,000
= Rs 1,09,500 after Meena approves; out-of-stock and price-drift injections both
escalate (the 9.3-point gap is over the 5-point threshold); with the gap forced
to 3 points the agent swaps to KraftPro AND re-confirms it, locking Rs 1,04,500;
a Rs 21.95 fallback is refused on the money ceiling; and calling `confirm()` on a
RANKED or AWAITING_APPROVAL transaction raises rather than acting. Golden test
still 14 green.

### Stage 7: mock payment (`agent/payment.py`)

The only stage that spends money, and CLAUDE.md's whole spec for it is three
words: retry once, then fall back.

```
attempt 1 declined  ->  retry: a declined card is usually transient
attempt 2 declined  ->  stop. This option cannot be bought.
cannot be bought    ->  escalation trigger 3, the same handler stage 6 uses
```

`pay(context, audit, confirmation, overrides=None)` is the stage. Note the third
argument: **it takes stage 6's ConfirmationOutcome rather than digging it out of
the context**, so you physically cannot call this function without a confirmation
in your hand. The ordering of stages 6 and 7 is enforced by the signature instead
of by everyone remembering.

**It pays for the locked quote and nothing else.** The amount comes from the
ConfirmationOutcome — never recomputed from the ranked product, never re-read
from the catalog. Stage 6 already settled "is this still the price?"; asking again
here would be a second, competing answer, and the gap between the two answers is
where a customer gets charged a figure nobody approved.

**A retry is a retry, not a fresh decision.** Same lock reference, same amount,
same product. Verified: both attempts in the demo carry one lock and one figure.
Retrying is also capped at `MAX_ATTEMPTS_PER_LOCK = 2` — a named constant rather
than a loop condition, so "retry once" is checkable in one place. Retry loops are
how real systems produce duplicate charges.

**The fallback gets its own lock before it gets paid for.** When both attempts
fail and the handler swaps to the next eligible product, that product goes back
through stage 6 for a fresh price check, stock check and lock reference. You
cannot pay for something you never confirmed, and paying for one thing with the
paperwork for another is exactly the failure stage 6 exists to prevent. It also
means the fallback passes the authorised money ceiling on the way through.

**The declines are logged as they happen**, not summarised afterwards. That is
what makes "we retried once" checkable rather than asserted — the decline entry
exists even if the retry then throws. Finance is on the notify list for a
completed payment but not for a decline: a decline is an operational hiccup, a
payment is a number that has to reconcile against somebody's books.

**The gateway has no randomness in it.** It reads two switches and returns
approved or declined. A demo that pays on some rehearsals and not others is a
demo you cannot rehearse.

A fourth switch was added to config.yaml for this stage:

| Switch | Default | What it shows |
|---|---|---|
| `decline_first_payment_attempt` | **on** | the scripted demo — one decline, retry succeeds on screen |
| `decline_every_payment_attempt` | off | a dead card, so both attempts fail and trigger 3 becomes visible |

Both apply only while paying for the FIRST locked product, the same rule
vendor.py uses for its quote switches: **an injected failure is one bad day, not
a curse on the whole run.** If the dead-card switch killed every card, the
fallback path would be unreachable — and that is the path the rule exists to
demonstrate.

**One wording bug caught in testing, worth knowing about.** A fallback paid on
its first attempt has declines behind it in the transaction, but none of them
were ITS declines — they belonged to the option it replaced. Counting all
declines together produced the sentence "the first attempt was declined; the
retry succeeded" about a payment that was approved first time. The retry story is
now told from the winning lock's own attempt number, with earlier failures
reported as what they actually were.

Verified on the demo brief: the scripted run declines once and pays Rs 1,09,500
on attempt 2 (`PAY-TXN-####-02`, same lock, same amount); switch off and it pays
first time; a dead card with the real 9.3-point gap declines twice and escalates
with nothing bought; with the gap forced to 3 points it declines twice, falls back
to KraftPro, RE-LOCKS it at stage 6 and pays Rs 1,04,500 on that lock's first
attempt. Paying an unconfirmed or already-escalated transaction raises rather than
acting. Golden test still 14 green.

**Stages 5 → 7 now read back as one audit trail**, which is the stage-8 view:

```
01  ESCALATION  AGENT  5   Rs 1,09,500 exceeds the Rs 1,05,000 limit by Rs 4,500; held.
02  DECISION    USER   5   Meena approved the Rs 1,09,500 order; resuming at confirmation.
03  ACTION      AGENT  6   Re-validated at Rs 21.90, 20,000 in stock. Locked as LOCK-...
04  ACTION      AGENT  7   Attempt 1 of 2 declined. Retrying once, same lock, same amount.
05  ACTION      AGENT  7   Paid Rs 1,09,500 against lock LOCK-... on attempt 2.
```

notify list across the run: `['requester', 'finance']`.

### Stage 8 and the app (`agent/close.py`, `app.py`)

**`close.py` exists because no stage marks its own work finished.** Stage 6 leaves
the transaction CONFIRMING, stage 7 leaves it PAYING. A stage that declared itself
complete would be the one stage nobody else verified, so closing is its own step:
final entry, requester and finance notified, status COMPLETED — the only place
that status is ever set.

It computes nothing. Every figure is read from what earlier stages recorded, and
whether a human signed is read off a USER-actored DECISION entry in the trail
rather than inferred from the amount (the total only says whether one was
*needed*). It refuses to close an escalated transaction: that stays parked with
its state intact, because tidying up is not approval either.

**`app.py` is four tabs and no business logic.** Every number on screen is read
off an object a stage already produced. The engine is called from exactly two
functions — `handle_message()` for stages 0-5 and `execute()` for stages 6-8 —
and the screens only read `st.session_state`. That split is what stops a
Streamlit re-run from re-running discovery or paying twice.

| Tab | Shows |
|---|---|
| 1 · Brief | chat intake, parsed fields with HARD/SOFT class and CONFIRMED/ASSUMED provenance, the four weights and where each came from |
| 2 · Comparison | 7 considered / 3 qualified, the ranked table, **every score term expandable**, and the 4 rejections with reasons |
| 3 · Decision | the limit check as four metrics, the escalation, Approve / Decline / Let it expire, then stages 6-8 as they happen |
| 4 · Audit | the trail, the notify list, the one-page auditor view, both downloads, and a replay read back from the .jsonl |

The sidebar carries the transaction id and status, an honest parser label (Gemini
reachable, or "offline parser · no API key found"), and the four failure switches.

**Verified by driving the real app** with Streamlit's own `AppTest` harness — not
by reading the code and hoping. Seven scenarios, no exceptions on any screen:

1. cold start renders 4 tabs, 3 buttons, 4 switches
2. off-topic message -> declined with the scope statement, nothing started
3. the demo brief -> **58.0 / 48.7 / 33.7 through the live Gemini parser**, gap
   9.3, Rs 1,09,500, status `awaiting_approval`, 5 audit entries
4. Approve -> lock, payment declined once then approved, closed at 10 entries
5. out-of-stock switch -> stage 6 escalates instead, nothing paid
6. Decline -> `declined`, nothing bought
7. Let it expire -> `expired`, nothing bought

**To run it:** `python -m streamlit run app.py`

---

## Numbers that must not drift

These are what the demo path is built on. Three of the eight demo steps only
happen because of these exact figures, so if the code disagrees with them the
demo has quietly changed behaviour — see "the golden test" below.

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

**Why it matters is the demo, not the deck.** Judges watch the run, not the
slides, so "the code disagrees with the deck" is the wrong reason to care about
this test. The right one: three of the eight demo steps only happen because of
these exact numbers.

| Demo step | Only happens because |
|---|---|
| 4 — ranked comparison, every score term visible | the table IS 58.0 / 48.7 / 33.7 |
| 5 — over the limit, approval screen fires | 5,000 x Rs 21.90 = Rs 1,09,500 > Rs 1,05,000 |
| 6-7 — confirmation/payment fails, agent escalates | the 9.3-point gap beating the 5-point threshold |

If Corusafe stops winning or its price shifts, **the approval screen never
fires** — and that screen is the whole "autonomy the user can audit" moment. We
would not find out from a wrong number on a slide. We would find out on stage,
when the agent quietly bought the boxes instead of asking Meena.

The scenario it actually protects against: twenty minutes before presenting,
someone nudges a catalog price or a weight to make a screen look nicer, and the
ranker now picks KraftPro. Half a second of `pytest` says so. Nothing else in the
project will.

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
