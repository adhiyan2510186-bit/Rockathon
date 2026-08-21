# EFFICIENCY.md — the engineering claims we make on stage

This is the file we read from when a judge asks "is this actually efficient, or
just tidy?" Every entry carries a **file path with a line number**, so we can
open the code the moment the question lands.

## The rule we hold ourselves to

> **State only what we have measured. Never state a complexity claim that the
> data size does not justify.**

Our catalogs hold 57 products across six sources, and the largest single
category is 17. Announcing "O(n log n)" at that size invites one obvious
question — *"why does that matter here?"* — and we lose more credibility
answering it than the claim ever won us. So every number below was measured on
this repo, and the method is written down next to it.

Where a win is about **shape** rather than speed at today's size, we say that
plainly: it is the difference between code that stays correct as the catalog
grows and code that quietly degrades.

---

## 1 · The decision path contains zero LLM calls

**Claim.** One language-model call per brief. Everything that decides anything —
filtering, weights, ranking, the limit check, escalation — is plain Python.

**Where.** `agent/language.py` is the only module that touches a model.
`agent/ranking.py`, `agent/discovery.py`, `agent/authorisation.py` and
`agent/escalation.py` import it nowhere. `tests/test_ranking.py` runs the entire
chain with the offline parser, no key and no network, and asserts the same
58.0 / 48.7 / 33.7 every time.

**What the naive version does.** Most agent demos chain calls: one to parse, one
to compare products, one to "reason about" the best option, one to explain the
choice. That is four or more round trips, four chances to hallucinate a number,
and a different answer on every run.

**Measured.** The full decision path — parse the brief, weight it, discover and
filter 17 products from six sources, rank, read the market signal, check the
authorisation limit, and write every audit entry to disk — runs in **9.5 ms**,
averaged over 100 consecutive runs after a warm cache, with zero network calls.

How it was measured: the loop in the block below, run from the repo root. It is
the same sequence `app.py` executes for a brief.

```python
# 100 runs of parse -> authorise, audit writes included
for _ in range(100):
    ctx = TransactionContext(transaction_id=audit.new_transaction_id())
    log = audit.AuditLogger(ctx)
    ctx.brief = language.extract_brief(BRIEF, log, force_offline=True).brief
    ctx.weights = weights.compute(ctx.brief, log)
    ctx.filter_results = discovery.run(ctx.brief, log)
    ctx.ranked = ranking.rank(eligible, ctx.weights, cap, days, log)
    signals.read(ctx.ranked, ctx.brief, log)
    authorisation.authorise(ctx, log)
```

Re-ranking after an approval costs nothing at all, which is why a user can
approve twenty minutes later and resume instantly.

**The sentence for stage.** *"Re-running our ranking is free and gives a
byte-identical answer. Re-running a prompt is neither."*

---

## 2 · Catalogs are parsed once — except where detecting change is the job

**Claim.** Each vendor catalog is read and normalised once per session and held,
**except** at stage 6, which deliberately forces a genuine re-read.

**Where.**
- Cache: `agent/sources.py:105` — `SourceAdapter.fetch()`, the `fresh or self._cache is None` branch at `agent/sources.py:111`.
- The deliberate exception: `agent/vendor.py:423` — `_live_record()` calls `discovery.discover(product.category, fresh=True)`.

**What the naive version does.** Before the adapter layer, `discover()` re-read
and re-parsed both files on *every* call — once for stage 3, again for the
category lookup during escalation, again for every fallback re-validation, and
again at confirmation. Same bytes, same answer, re-derived each time.

**Measured.** A demo-shaped run (stage 3 → escalation lookup → fallback
re-validation → stage 6):

| | catalog file reads |
| --- | --- |
| Before (re-read per call) | **8** |
| After | **4** |

Of the 4 remaining, **2 are the initial parse and 2 are the stage-6 re-read we
refuse to cache.** The read-only path went from 6 reads to 2.

**Why the exception is the interesting half.** Stage 6 exists to catch a price or
stock value that moved since stage 3. Serving that from cache would compare our
copy against our own copy and pass every single time — a formality, not a check.
Locked down by `tests/test_sources.py::test_stage_six_bypasses_the_cache`.

**The sentence for stage.** *"We cache aggressively everywhere except the one
place where caching would be a correctness bug — and there we cache nothing on
purpose."*

---

## 3 · The hard gate normalises the brief once, not once per product

**Claim.** Values that are constant across the whole candidate pool are computed
above the loop instead of rebuilt inside it.

**Where.** `agent/discovery.py:138` — `apply_hard_gates()`. The
`required_specs` frozenset and the four cap values are built at
`agent/discovery.py:164`, before the `for product in products` loop.

**What the naive version does.** The previous version rebuilt the brief's
required-spec set *inside* the loop, once per product. The brief does not change
between products, so that was `n × m` string normalisations to learn `m` facts —
work whose answer was identical every time.

**Shape.** `O(n·m)` → `O(n + m)` on the brief-side normalisation.

**Measured.** Running the demo brief through the full pipeline:

| | `spec_key()` calls | actual regex normalisations |
| --- | --- | --- |
| Before | 43 | 43 |
| After | 25 | **7** |

Two separate wins stacked: hoisting the set out of the loop cut calls 43 → 25,
and the cache on `spec_key` (`agent/discovery.py:58`) cut real work 25 → 7. The
spec vocabulary is small and fixed, so after the first pass every lookup is a
dictionary hit instead of a regex substitution.

**Honest scope.** At 17 products in a category this saves microseconds. We are
not claiming a speed-up a judge could feel. We are claiming the loop does not
repeat work whose answer cannot change — which is what keeps it correct at 17
products and at 7,000.

---

## 4 · Adding a vendor touches one file

**Claim.** The pipeline does not know where products come from, so a new source
is one new class and zero edits anywhere else.

**Where.** `agent/sources.py` — the `SourceAdapter` interface, and the registry
at `agent/sources.py:443`. `agent/discovery.py` imports `sources` and never
mentions JSON or CSV.

**What the naive version does.** Before this, `discovery.py` contained
`load_packhub()` and `load_boxbazaar()` by name. "Add a vendor" meant editing the
middle of the decision pipeline — the file that also holds the hard gate.

**Not a speed claim.** This one is about blast radius, and we should say so.
The measurable version: `tests/test_sources.py` asserts that toggling a source
changes the candidate pool **and nothing downstream** — same ranker, same audit
schema, same scores for whatever survives.

**Measured — six vendors, three read() methods.** We grew the catalog from 21
products across two vendors to **57 across six** (packaging 17, laptops 16,
headphones 14, furniture 10) by adding two marketplace feeds, Amazon and
Flipkart, in a third schema shape. The diff:

| Added | Cost |
| ----- | ---- |
| A third feed FORMAT (`MarketplaceJsonAdapter`) | one class, `agent/sources.py:220` |
| Two vendors on that format (`AmazonAdapter`, `FlipkartAdapter`) | four lines each — key, display name, path |
| A fourth category (headphones) | one `config.yaml` block, plus catalog rows |
| 36 new products with reviews, messy titles and hub-based delivery | data files only |

`agent/ranking.py`, `agent/discovery.py`, `agent/authorisation.py`,
`agent/escalation.py`, `agent/signals.py` and `agent/audit.py` were **not
modified**. `tests/test_sources.py::test_a_second_vendor_on_a_known_format_costs_no_new_parsing`
asserts the ratio directly: six adapters, three distinct `read` implementations.

**The sentence for stage.** *"The live vendor API we did not build is one file.
That is not a promise — the toggle you just watched is the same seam."*

---

## 3b · Answering the usage question resumes at stage 2, not stage 0

**Claim.** When the agent asks what a purchase is for, answering re-enters the
pipeline at the weight engine. The sentence is never parsed twice and the
language model is never called twice.

**Where.** `app.py:304` — `apply_context()`, which sets the tag on the brief
already sitting in the transaction context and calls `rank_and_authorise()`
(`app.py:333`). `handle_message()` stops at `weights.context_needed()` before it
computes anything.

**What the naive version does.** Re-sends the full sentence through
`check_scope()` and `extract_brief()` on every answer, because that is the
shortest path from "user clicked something" back to "we have a ranking". That is
**two extra LLM calls per answer** — one for the scope gate, one for extraction —
and worse than the cost, it means the brief could come back different. A model
that reads the same sentence twice is not guaranteed to fill the same fields,
so the pool you were shown might not be the pool you approve.

**Measured — call counts on the headsets brief:**

| | LLM calls | catalog reads |
| --- | --- | --- |
| Type the brief | 2 (scope + extract) | 0 — nothing discovered yet |
| Answer the question | **0** | 6, once, cached thereafter |
| Answer a *different* way (new run) | 0 for the re-rank | 0 — catalogs held |

`tests/test_app.py::test_answering_the_question_ranks_without_rereading_the_brief`
asserts it structurally: every field of the brief except `context_tag` is
byte-identical before and after the answer.

**The related shape win.** `rank_and_authorise()` exists because there are now
two ways into stages 2–5 — a brief that needed no question, and one that just
had its answered. One function, two call sites. Two copies of those thirty lines
is exactly how the context path quietly stops matching the normal path.

**The sentence for stage.** *"Answering a question about preference cannot change
what she asked for, so we do not re-derive it. Same reason approving an order
resumes at confirmation instead of starting the search again."*

---

## 4b · The marketplace conversions the other two feeds never needed

**Claim.** Each awkward shape a feed arrives in is handled in exactly one
function, and adding a format adds functions rather than branches.

**Where.** `agent/sources.py` — `rupees_from_display()` (`"Rs 1,14,900"` →
`114900.0`), `_date_keyed()` (a history keyed by date rather than listed), and
the reuse of `worst_case_days()` on prose (`"Ships from Delhi NCR - Delivered in
11-12 days"` → `12`).

**What the naive version does.** Two things we specifically avoided. First, a
`float(price.strip("Rs "))`, which reads `"Rs 1,14,900"` as a crash — or worse,
a "helpful" `replace(",", "")` that assumes three-digit grouping and is simply
wrong on Indian lakhs. Second — the real temptation — **reading specs out of the
listing title**. `AMZ-LAP-1101`'s title advertises `8GB DDR4` inside a 120-character
marketing string, and parsing it is thirty seconds of regex. We read `spec_sheet`,
the structured field, and treat the title as display text.

**Measured — reuse, counted.** The marketplace format required **two new parsing
functions**; the other three conversions it needs (`worst_case_days`,
`first_number`, and the category lowering) were already written for the
aggregator CSV and are called unchanged. `_date_keyed` sorts by date rather than
trusting file order, because a JSON object promises no ordering and stage 4.5
reads `series[-1]` as "latest" — an unsorted series would invert every trend on
screen with nothing to show that it had.

**The sentence for stage.** *"Three feeds, and the same fact arrives as a number,
an inconvenient unit, and a string a human was meant to read. Every one of those
conversions lives in one function you can open."*

---

## 4c · Buyer reviews cost the decision path nothing, by construction

**Claim.** The catalog carries ~100 review snippets and the ranker does not read
one. Adding them changed no scoring code and cannot change a score.

**Where.** `agent/models.py` — `ReviewSnippet`, and `Product.review_count` /
`Product.sample_reviews`. Rendered at `ui/components.py:364`, `buyer_reviews()`,
called from exactly one place in `app.py` — inside the score-breakdown drill-down.

**What the naive version does.** Sends the review text to the LLM and folds a
sentiment score into the ranking. That is one extra model call per product per
run — for our headphones pool, **five calls where we make zero** — and it makes
the ranking non-deterministic, so the same brief stops producing the same table.

**Measured — a guarantee, not a benchmark.**
`tests/test_reviews_are_advisory.py` runs four checks: stripping every review
leaves the ranking byte-identical; replacing the winner's reviews with three
furious one-stars leaves its score unchanged; a product with zero reviews is not
penalised for the silence; and a static AST pass fails if any module in `agent/`
outside `models.py` and `sources.py` so much as names `sample_reviews`.

**The sentence for stage.** *"The star rating is a number, so we score it. The
sentences are not, so we show them to you instead. That distinction is the whole
project, and there is a test that fails the moment we blur it."*

---

## 5 · Append-only audit, written at the moment of the event

**Claim.** Every audit entry is one append. No state is reconstructed after the
fact, and replaying a transaction is a file read, not a recomputation.

**Where.** `agent/audit.py` — `AuditLogger`, bound to a single transaction.
Each event method appends to the in-memory context *and* writes one JSONL line.

**What the naive version does.** Reconstructing a decision trail at the end by
walking back over the final state. That is cheaper to write and worthless: an
assumption logged after the purchase is a story, not a record.

**Measured.** `O(1)` per event, one line appended. A completed transaction
replays from `exports/TXN-*.jsonl` with no agent code in the loop — the finance
view in the UI is reading the file, not re-deriving anything.

---

## 6 · Four "best in pool" badges from one pass, not four sorts

**Claim.** Stage 4.5 works out which product is cheapest, fastest, most reliable
and has the longest replacement window by walking the pool **once**.

**Where.** `agent/signals.py:351` — `_pool_positions()`. All four bests are
tracked in a single `for product in products` loop.

**What the naive version does.** Sort the pool four times, once per criterion,
and take the head of each — `4 × O(n log n)`, plus four throwaway lists. It is
the obvious way to write it and it re-walks the same data four times to answer
four questions that could be answered together.

**Shape.** `O(n · k)` with `k = 4` fixed, versus `O(k · n log n)`. One traversal,
constant extra memory.

**A correctness decision inside it, worth mentioning.** Only a *strictly unique*
best earns a badge. If two products tie on price, neither is labelled "cheapest"
— a badge on two rows tells a reader nothing, and "joint cheapest" is not a
distinguishing fact. Locked by
`tests/test_signals.py::test_pool_chips_go_only_to_an_outright_winner`.

**Honest scope.** At n=3 survivors this is not a speed story. It is the same
principle as entry 3: answer every question you can from the traversal you are
already doing.

---

## 7 · The market signal is structurally unable to change the decision

**Claim.** Stage 4.5 cannot alter eligibility, score, ranking or authorisation —
not by policy, but because it has no reference to anything that holds one.

**Where.** `agent/signals.py:390` — `read()` takes the ranked list as read-only
input and returns a separate `MarketRead` keyed by product id. The module imports
neither `agent.ranking` nor `agent.authorisation`.

**Why this belongs in an efficiency file.** Because it is also why the signal is
free to re-run. It derives from immutable `Product` data and never feeds back
into the pipeline, so recomputing it costs nothing and can never desynchronise
the ranking from what the user was shown.

**Measured.** `tests/test_signals.py` runs the full pipeline twice — once with
signals, once without — and asserts the ranking and the authorisation outcome are
identical. Plus a structural test asserting the imports are absent, so a future
edit that reaches for the ranker fails at the test, not on stage.

**The sentence for stage.** *"Our winner is flagged 'order today' and it still
escalates for human approval, because the order is over the limit. Urgency
changes priority. It never changes authority."*

---

## 8 · Adding a category is data, not code

**Claim.** Teaching the agent a new kind of purchase costs one config block and
some catalog rows. It costs zero new branches, zero new code paths, and zero
changes to filtering, ranking, market signals, authorisation or audit.

**Where.** `config.yaml` — the `categories:` block, one entry per category
carrying its price cap, default weights, trigger keywords, spec vocabulary and
trade shorthand. `agent/config.py:_category()` is the single lookup every one of
those settings goes through, and `agent/config.py` is the only file in `agent/`
that names a category at all.

**What the naive version does.** Two things, both of which we had. First, the
settings were spread across two flat tables (`per_unit_cap_defaults_inr` and
`category_default_weights`) plus three module-level dicts in two Python files, so
"add a category" meant finding all five. Second — the version we did not build —
an `if category == "laptops"` in the ranker or the UI, which works for the second
category and collapses on the fourth.

**Measured — the count, not the clock.** Furniture and laptops were added in the
same commit as their catalogs. The diff touched `config.yaml`, two new data
files, and four lines of `agent/sources.py` (two adapters × key, display name,
path). `agent/ranking.py`, `agent/authorisation.py`, `agent/escalation.py`,
`agent/signals.py`, `agent/audit.py` and `agent/models.py` were **not modified**.
`tests/test_category_is_data.py` holds that down: it parses every module in
`agent/`, strips comments and docstrings, and fails if a category name survives
as a live string literal outside `config.py`.

**The four soft criteria are deliberately not configurable.** Reliability, price,
replacement and delivery are the same in every category, because they are the
universal commercial questions — a replacement window *is* a warranty on a chair.
Making them per-category would have meant a cross-cutting edit across the ranker,
the weight engine, every weight table and the chart palette, in exchange for
nothing a buyer would notice. Specs are what differ between categories; buying
criteria are not.

**The sentence for stage.** *"That block is the entire furniture implementation.
The engine has never heard of a mailer box — and there is a test that fails if
anyone teaches it one."*

---

## 1b · The model call can be switched off without switching the product off

**Claim.** A run can be told to skip the language model entirely, and the request
is never sent — not sent and ignored. Two API calls saved per brief, and nothing
downstream changes.

**Where.** `agent/language.py:522`, `_use_model()` — the one line that decides
whether a call happens. `agent/language.py:589`, `_skipped_note()` says which of
the two reasons applied. `app.py:520`, `_language_switch()` is the sidebar
control, and `app.py`, `handle_message()` reads the flag **once** and passes it
to both `check_scope()` and `extract_brief()`, so a single brief can never be
half-read by the model and half by the word matcher.

**What the naive version does.** Two versions are worse, and we had the first
one. (a) The only way to save quota is to delete the API key, which also makes
the app look like it has no AI in it — you cannot demonstrate the thing you
turned off. (b) Call the model anyway and discard the answer on a flag, which
costs exactly as much as not having the switch.

**Measured.** Reading one brief costs **2 calls** — the scope check at stage 0
and the extraction at stage 1. Gemini's free tier bills per day, so the switch
is worth 2 calls × however many times we rehearse. `tests/test_brief_parsing.py`
asserts the negative directly: with a key present and `_call_gemini` replaced by
a function that raises, `force_offline=True` completes both stages.
`tests/test_app.py` does the same through the real checkbox.

**Why it cannot change an answer.** Both parsers return the same `_Extraction`
and go through the same `_to_brief()`. `test_forcing_offline_changes_who_read_
it_and_nothing_else` asserts the two Briefs are equal field for field, which is
the claim the sidebar caption makes to the user.

**The sentence for stage.** *"The model reads the sentence. It does not decide
anything — so we can turn it off mid-demo and show you the same ranking, with
the app telling you plainly that it is reading your words itself."*

---

## How to add to this file

Log the win **when you write the code**, not at the end. Each entry needs:

1. The claim, in one plain sentence
2. `path/to/file.py:line` and the function name
3. What the naive version would have done
4. A measured number, and how it was measured — or an explicit note that this
   one is about shape rather than speed
