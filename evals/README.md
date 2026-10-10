# Evals

Measures whether a prompt or model change made the analysis better or worse,
instead of guessing from one hand-checked run.

Scraping is stubbed with saved HTML so the input never varies. Everything after
the fetch — the same agents, prompts, contracts and critic that run in
production — is real, which is why `NVIDIA_API_KEY` is required.

## Running

```bash
uv run python -m evals.run                      # all cases, all stages
uv run python -m evals.run --case stripe        # one case
uv run python -m evals.run --stages profile     # one stage, fast
uv run python -m evals.run --suite market       # macro -> sizing -> resources
uv run python -m evals.run --no-critic          # halve the LLM calls
uv run python -m evals.run --repeat 3           # every case 3 times: mean and spread
uv run python -m evals.run --check              # fail on regression
uv run python -m evals.run --update-baseline    # record a new baseline
uv run python -m evals.run --render evals/results/latest.json   # no key, no calls
uv run python -m evals.run --model moonshotai/kimi-k3 --label model-kimi   # every stage on one model
uv run python -m evals.models evals/results/model-*.json           # compare models, no calls
```

### Choosing models

`--model` puts every stage on one model and leaves the critic on its own, so
every candidate is judged by the same reviewer. Run each candidate with
`CRITIC_RETRY_THRESHOLD=0`, so a re-run cannot hide a weak first answer, then
`evals.models` ranks them per stage on *quality*: the mean of the contract score
and the critic's confidence, with a stage that failed or was never reached
counting 0. Pick per stage, then set `AGENT_MODEL_*` (or the constants in
`app/config.py`). Candidates run in parallel without trouble: one process each.

Ask the API what exists, not the client library: `GET /v1/models` lists far
more models than answer a chat completion, so probe before sweeping.

A full run is roughly 7 cases × 6 stages of real model calls, and `--repeat N`
multiplies that by N. Use `--stages` or `--case` while iterating, and save the
full run for before/after comparisons.

| Flag | Default | Meaning |
|---|---|---|
| `--repeat N` | 1 | Run every case N times. Each run's score is kept, and every (case, stage) cell gets a mean and standard deviation. |
| `--check` | off | Compare against the suite's baseline (see [Baseline](#baseline)). |
| `--floor X` | 5.0 | Smallest contract-score drop that can count as a regression. |
| `--k X` | 2.0 | How many standard errors a drop must exceed. |
| `--confidence-floor X` | 20.0 | Smallest per-stage critic-confidence drop that can count. |
| `--update-baseline` | off | Record this run. A `--case`/`--stages` run merges into the baseline. |

Exit codes: **0** ok, **1** regression, **2** nothing could be compared or run:
no baseline for the suite, no API key, an unknown case or stage, or an invalid
case file. `--check` with no baseline stops before calling any model.

CI runs the **Evals** workflow (`.github/workflows/evals.yml`) in three ways:

| Trigger | Runs |
|---|---|
| pull request touching `app/analysis/**`, `app/pipeline/contracts.py`, `app/llm/**` or `evals/**` | `--stages profile --repeat 3 --check`, the cheap gate |
| nightly schedule | the full BI suite, `--repeat 2 --check` |
| manual (`workflow_dispatch`) | whatever suite, cases, stages, repeats and check you choose |

Without the `NVIDIA_API_KEY` secret, which fork pull requests never get, the
job is skipped with a notice rather than failed. The report and the `--check`
verdict go to the run summary.

## Scoring

Three signals per stage:

| Signal | Cost | Catches |
|---|---|---|
| `completeness` | free | empty fields, short lists, placeholder text, echoed prompt examples |
| `confidence` | one small LLM call | ungrounded claims, generic filler, invented specifics |
| assertions | free | wrong company, wrong industry, missing offerings, forbidden strings, invented prices, a government site read as a business, disqualified entities |

`score = completeness − 15 × failed assertions`. A failed assertion is a factual
miss, so it should outweigh a slightly thin list.

With `--repeat`, report cells show `mean±stdev` across runs.

## Cases

`cases.yaml` holds one entry per fixture. Expectations are deliberately loose —
they pin down *what the analysis is about*, not how it is phrased, so a prompt
tweak does not fail the suite for no reason.

The set is chosen to cover different failure modes:

| Case | Why it's here |
|---|---|
| stripe | Dense marketing site, broad product range |
| ghost | Open-source project with a commercial hosted tier |
| eleventy | Free open-source tool, no prices on the page — must not invent pricing |
| linear | Script-heavy site; copy lives in the markup |
| basecamp | Very little text — tests behaviour when starved of input |
| flyio | Jargon-heavy developer infrastructure |
| govuk | **Not a business at all** — the hallucination canary |
| valcrest | Local B2B software, one-time TTD price, runs on-premise — must not be called cloud-based |
| riomed | Healthcare platform page that is mostly a product menu |
| asawright | Nonprofit land trust, 1,400 characters — a second not-a-business canary |
| angostura | Real business behind an age gate whose dropdowns dominate the text |
| tormach | Small manufacturer: B2B hardware, not software |
| pilot | Bookkeeping services sold like software |
| tartine | Bakery chain; short copy, mostly navigation |
| harvestright | Direct-to-consumer appliance storefront |
| allbirds | E-commerce storefront of product tiles — must still find the materials story |

The last nine (added 2026-10) are the sites this app is actually pointed at:
small and mid-sized businesses, several in Trinidad and Tobago. Their pages
were saved with scripts, styles, comments and inline images stripped, after
checking that extraction was identical to the live page.

Each case's purpose is backed by an assertion, not just stated:

- **eleventy**: no currency amount in the profile, marketing or report output
  (market-size figures like `$10–12 B` are allowed, because the report restates
  the market stage's TAM), and the market stage's `pricing_strategy` must say it
  is free, open source or community funded.
- **govuk**: the profile's `industry` must say government or public sector, and
  the profile and marketing output may not invent pricing tiers, premium plans,
  free trials or subscription fees.
- **self-hosted-cicd** (market suite): hosted SaaS such as CircleCI may not be
  listed as a resources key player, and Drone, Woodpecker, Concourse, Jenkins
  and Gitea/Forgejo must each appear. The output that shipped the original bug
  (`fixtures/known_bad/`) fails 5 of these 6 assertions.

### Stage assertions

Besides the profile-only fields (`company`, `industry_terms`, `must_mention`,
`min_offerings`) and `forbidden` (any stage), a case can carry `assertions`.
Each one targets any stage, and optionally a dotted field path:

```yaml
assertions:
  # Why this exists. Every entry gets a comment.
  - stage: market                  # or `stages: [profile, marketing, report]`
    path: pricing_strategy         # optional; lists are walked: key_players.name
    require_any_regex: ['\bfree', 'open[- ]?source']
```

The checks are `forbid` (literals), `forbid_regex`, `require_any` (literals) and
`require_any_regex`; a `require_any*` passes when any one of its terms or
patterns appears. Each check reads every string *value* in the stage output,
never the field names, so it keeps working when an output model changes shape.
Matching is case-insensitive. A `path` that no longer exists fails loudly.
Unknown stages, keys and bad regexes are rejected when the case file loads,
before anything is paid for. `tests/test_eval_assertions.py` checks every
shipped assertion against realistic right and wrong answers.

Fixtures are saved with `<script>`, `<style>` and `<svg>` stripped. The scraper
discards those anyway, so the fixture yields exactly what the live page would,
at a fraction of the size.

## What it has already caught

**1. The prompt's own example, returned as the answer.** Eleventy — a static
site generator — was classified as `"SaaS E-commerce Analytics"`, the literal
example from the business-profile prompt. Completeness scored it 100, because
the value is non-empty and looks specific; only the case assertion noticed.

The same failure ran through the market stage: *every* case returned
`"$500M-$2B"` for market size, straight out of that prompt's example.

Fixes: the prompts no longer offer copyable literals, and
`find_echoed_examples` in `app/pipeline/contracts.py` now penalises any field
returned verbatim from a prompt's example — so the next one costs nothing to
catch.

**2. A prompt fix that broke a different stage.** Rewording the market prompt to
avoid (1) produced *"name the segment and what bounds it (size, sector,
behaviour)"*. The model read that as a schema and answered with objects:

```json
"customer_segments": [{"name": "...", "bounds": "...", "behaviour": "..."}]
```

The field is `list[str]`, so validation rejected every attempt and the stage
failed outright on 3 of 7 cases — a 43% failure rate introduced by a fix.

It was missed because the fix was validated with `--stages profile`, which never
runs the market stage. **Validate the stage you changed, across all cases.**
Prompt wording that names attributes reads as a schema; say "one plain sentence
of text, not an object" when a string is wanted.

**3. Instructions the model quietly ignores.** The FAQ section asked for 5-8
questions and got 3-4 on every single case. An exact count ("exactly 6") lands
where a range does not. One case also repeated a recommendation verbatim.

**4. An intermittent stage failure, and a blind spot in diagnosing it.** The
marketing stage fails roughly 1 run in 5 — not on a particular case, and not
from truncation (responses use ~1,100–1,450 of 4,096 tokens and end cleanly).
When it fails, all three attempts fail together.

Chasing it exposed the real problem: `parse_json_response` returned `None` for
malformed JSON *and* for a schema mismatch, so the recorded error was the same
sentence either way. It now reports which — naming the offending field for a
validation failure — and the repair turn feeds that specific reason back
instead of a generic complaint.

The underlying flakiness is **not fixed**. It is now self-diagnosing: the next
occurrence records the field that broke, on the stage row and in the eval
report. That is the honest position — reproducing it on demand would have cost
more API budget than the finding is worth right now.

None of these were visible from spot-checking one report.

## Adding a case

1. Save the page: `curl -sL https://example.com -o evals/fixtures/example.html`
2. Strip non-content markup (script/style/svg) to keep the repo light.
3. Add an entry to `cases.yaml`, with an `assertions` entry for whatever the
   case is there to prove, plus a comment saying why.
4. Run `--case example --stages profile` and check the expectations are neither
   trivially true nor impossible.
5. Record it with `--case example --update-baseline --repeat 3`. This merges
   only that case's cells into the baseline.

Prefer cases that stress a *different* failure mode from the ones already here.
Another conventional SaaS site adds little.

## Baseline

`baseline.json` (BI) and `market-baseline.json` (market, not yet recorded)
hold the last accepted scores. `--check` against a suite with no baseline prints
`no baseline for suite X; run --update-baseline` and exits 2, not 1.

### The regression rule

Unchanged code moves a stage mean by up to 10.7 points and a single cell by up
to 75 between runs (`tasks/evidence/run-variance.txt`). A fixed 5-point gate
therefore fires on noise. Instead, every unit (each (case, stage) cell, each
stage mean and the overall mean) regresses only when

```
mean drop > max(floor, k × SE)        SE² = s_base² / n_base + s_now² / n_now
```

- `s` is the cell's sample standard deviation across repeated runs, and `n` is
  its number of runs. A side with a single run borrows the other side's `s`.
- A stage or overall SE is propagated from its cells' SEs through the averaging
  weights. Averaging seven noisy cells gives a much narrower limit than any one
  cell gets.
- `floor` defaults to 5.0 (`--floor`) and `k` to 2.0 (`--k`). Per-stage critic
  confidence uses the same rule with a floor of 20.0 (`--confidence-floor`),
  because it is noisier.
- **With no repeats on either side there is no `s`.** Every limit is then the
  floor, and the check prints a warning that single-run comparisons are noisy.
  Use `--repeat` for any comparison you mean to act on.

Measured on the stored runs: treating the two pre-repair runs as `--repeat 2`
against the current baseline, the noise cells (linear/swot −75, ghost/report
−67) are absorbed. The real report-stage failure that the JSON repair later
fixed (basecamp and govuk failing in both runs) is still flagged.

### Like-for-like subsets

A `--case` or `--stages` run is compared over exactly the (case, stage) cells
it ran. Every baseline aggregate is recomputed from those cells, so
`--stages swot` compares swot with swot, not with the six-stage overall.
Cells new to this run are listed and not compared.

### Format and updates

Format 2 keeps the old aggregate keys (`overall`, `per_stage`,
`per_stage_confidence`, `per_case`, `metric_health`) and adds
`"format": 2` plus `cells[case][stage]` with every run's `scores`,
`completeness` and `confidence` (and their mean and stdev, for reading).
Aggregates are always recomputed from the cells. Format-1 files, which hold
aggregates only, are still read. Against one, only units that cover exactly
what a stored number covers are compared.

`--update-baseline` after a full run replaces the baseline. After a `--case` or
`--stages` run it merges only those cells into the existing baseline and
recomputes the aggregates; a partial run can no longer overwrite the whole
file. Merging needs a format-2 baseline. The shipped `baseline.json` was
converted losslessly from the run it was recorded from
(`tasks/evidence/rebaseline-run3-post-repair.json`). Its aggregate keys are
unchanged and it has one run per cell.

Re-baseline deliberately, only after confirming a change is an improvement.
Prefer `--update-baseline --repeat 3`: a baseline with repeats tightens every
later check.
