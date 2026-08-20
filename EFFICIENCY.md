# EFFICIENCY.md — the engineering claims we make on stage

This is the file we read from when a judge asks "is this actually efficient, or
just tidy?" Every entry carries a **file path with a line number**, so we can
open the code the moment the question lands.

## The rule we hold ourselves to

> **State only what we have measured. Never state a complexity claim that the
> data size does not justify.**

Our catalogs hold single-digit products. Announcing "O(n log n)" at n=7 invites
one obvious question — *"why does that matter here?"* — and we lose more
credibility answering it than the claim ever won us. So every number below was
measured on this repo, and the method is written down next to it.

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

**Measured.** `python -m pytest -q` runs the full pipeline **24 times in 0.26 s**
with zero network calls. Re-ranking after an approval costs nothing at all,
which is why a user can approve twenty minutes later and resume instantly.

**The sentence for stage.** *"Re-running our ranking is free and gives a
byte-identical answer. Re-running a prompt is neither."*

---

## 2 · Catalogs are parsed once — except where detecting change is the job

**Claim.** Each vendor catalog is read and normalised once per session and held,
**except** at stage 6, which deliberately forces a genuine re-read.

**Where.**
- Cache: `agent/sources.py:100` — `SourceAdapter.fetch()`, the `fresh or self._cache is None` branch at `agent/sources.py:106`.
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

**Where.** `agent/discovery.py:127` — `apply_hard_gates()`. The
`required_specs` frozenset and the four cap values are built at
`agent/discovery.py:136`, before the `for product in products` loop.

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
and the cache on `spec_key` (`agent/discovery.py:66`) cut real work 25 → 7. The
spec vocabulary is small and fixed, so after the first pass every lookup is a
dictionary hit instead of a regex substitution.

**Honest scope.** At n=7 products this saves microseconds. We are not claiming a
speed-up a judge could feel. We are claiming the loop does not repeat work whose
answer cannot change — which is what keeps it correct at 7 products and at 7,000.

---

## 4 · Adding a vendor touches one file

**Claim.** The pipeline does not know where products come from, so a new source
is one new class and zero edits anywhere else.

**Where.** `agent/sources.py` — the `SourceAdapter` interface, and the registry
at `agent/sources.py:220`. `agent/discovery.py` imports `sources` and never
mentions JSON or CSV.

**What the naive version does.** Before this, `discovery.py` contained
`load_packhub()` and `load_boxbazaar()` by name. "Add a vendor" meant editing the
middle of the decision pipeline — the file that also holds the hard gate.

**Not a speed claim.** This one is about blast radius, and we should say so.
The measurable version: `tests/test_sources.py` asserts that toggling a source
changes the candidate pool **and nothing downstream** — same ranker, same audit
schema, same scores for whatever survives.

**The sentence for stage.** *"The live vendor API we did not build is one file.
That is not a promise — the toggle you just watched is the same seam."*

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

## How to add to this file

Log the win **when you write the code**, not at the end. Each entry needs:

1. The claim, in one plain sentence
2. `path/to/file.py:line` and the function name
3. What the naive version would have done
4. A measured number, and how it was measured — or an explicit note that this
   one is about shape rather than speed
