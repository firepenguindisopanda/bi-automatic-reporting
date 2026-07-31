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
uv run python -m evals.run --no-critic          # halve the LLM calls
uv run python -m evals.run --check              # fail on regression
uv run python -m evals.run --update-baseline    # record a new baseline
```

A full run is roughly 7 cases × 6 stages of real model calls. Use `--stages` or
`--case` while iterating, and save the full run for before/after comparisons.

CI has a manual **Evals** workflow (`workflow_dispatch`) that does the same and
publishes the table to the run summary.

## Scoring

Three signals per stage:

| Signal | Cost | Catches |
|---|---|---|
| `completeness` | free | empty fields, short lists, placeholder text, echoed prompt examples |
| `confidence` | one small LLM call | ungrounded claims, generic filler, invented specifics |
| assertions | free | wrong company, wrong industry, missing offerings, forbidden strings |

`score = completeness − 15 × failed assertions`. A failed assertion is a factual
miss, so it should outweigh a slightly thin list.

## Cases

`cases.yaml` holds one entry per fixture. Expectations are deliberately loose —
they pin down *what the analysis is about*, not how it is phrased, so a prompt
tweak does not fail the suite for no reason.

The set is chosen to cover different failure modes:

| Case | Why it's here |
|---|---|
| stripe | Dense marketing site, broad product range |
| ghost | Open-source project with a commercial hosted tier |
| eleventy | Free tool, no revenue model — must not invent pricing |
| linear | Script-heavy site; copy lives in the markup |
| basecamp | Very little text — tests behaviour when starved of input |
| flyio | Jargon-heavy developer infrastructure |
| govuk | **Not a business at all** — the hallucination canary |

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
3. Add an entry to `cases.yaml`.
4. Run `--case example --stages profile` and check the expectations are neither
   trivially true nor impossible.
5. Re-baseline with `--update-baseline`.

Prefer cases that stress a *different* failure mode from the ones already here.
Another conventional SaaS site adds little.

## Baseline

`baseline.json` records the last accepted scores. `--check` fails when the
overall mean or any stage mean drops by more than 5 points — loose enough to
absorb model nondeterminism, tight enough to catch a prompt regression.

Re-baseline deliberately, only after confirming a change is an improvement.
