# Model liveness, 2026-08-26

## Why this is here

NVIDIA retired `meta/llama-3.1-8b-instruct` and the 49B behind competitive
analysis at 09:00Z, mid-session. Both models the app used stopped answering. The
same thing will happen again, so this records how to find replacements fast.

## Ask the API, not the client library

`langchain_nvidia_ai_endpoints.available_models` is compiled into the package and
still listed both retired models. That is where the misleading "141 models, 131
dead" reading came from, and it hid `openai/gpt-oss-20b` (the model eventually
chosen) because it is not in that list at all.

```bash
curl -H "Authorization: Bearer $NVIDIA_API_KEY" \
     https://integrate.api.nvidia.com/v1/models
```

`models-listed-2026-08-26.json` is that response: 83 entries, both retired models
correctly absent.

## The listing is a filter, not a promise

Probing all 58 chat candidates through `POST /v1/chat/completions`, only **14
answer**. 40 return `404 Not Found`, 2 return `500`, 1 was throttled.
`model-liveness-2026-08-26.json` has the per-model status.

Re-run it with:

```bash
uv run python tasks/evidence/probe_model_liveness.py "$NVIDIA_API_KEY"
```

It reads `models.json` from its own directory, so refresh that with the curl
above first. Costs 8 completion tokens per model.

## Callable on the day

`openai/gpt-oss-20b`, `openai/gpt-oss-120b`, `moonshotai/kimi-k3`,
`nvidia/nemotron-3-super-120b-a12b`, `nvidia/nemotron-3-ultra-550b-a55b`,
`nvidia/nemotron-3-nano-30b-a3b`, `nvidia/nemotron-3.5-lightning-30b-a3b`,
`nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`,
`meta/llama-3.2-11b-vision-instruct`, `meta/llama-3.2-90b-vision-instruct`,
`stepfun-ai/step-3.7-flash`, `poolside/laguna-xs-2.1`,
`google/diffusiongemma-26b-a4b-it`, `meta/muse-glimmer-30b`.

## Answering "ok" is not evidence

Every candidate was then driven through the app's own JSON path on the real
profile stage. All eight fast ones parsed, **and completeness read 100.0 for
every one of them**, which is exactly the pinned metric M1 exists to expose, met
in a decision that depended on it. Confidence was the only metric that could
rank them:

| model | confidence (stripe · ghost · govuk) | mean | s/stage |
|---|---|---|---|
| `openai/gpt-oss-20b` | 95 · 95 · 95 | **95.0** | **7.4** |
| `openai/gpt-oss-120b` | 82 · 86 · 94 | 87.3 | 115.3 |
| `nvidia/nemotron-3-nano-30b-a3b` | 60 · 95 · 95 | 83.3 | 7.4 |
| `meta/llama-3.2-11b-vision-instruct` | 78 · 85 · - | 81.5 | 8.7 |

Dropped on latency before quality mattered: `kimi-k3` 55.6s,
`step-3.7-flash` 86.9s, `nemotron-3.5-lightning-30b-a3b` 188.2s per stage.

Caveats kept on purpose: the 120b's 115.3s is inflated by a `504` and retry;
llama's missing third score is a critic call that died on a `503`; and
`95 · 95 · 95` is flat enough to watch, though the critic gave 60/78/82/86/94 to
the others on the same inputs, so it is discriminating rather than stuck.

## 429 is per model, and it lifts

`minimaxai/minimax-m3` was the only `429` in the whole sweep, sustained about two
hours while 13 other models answered normally in the same second. No
`Retry-After`, no `X-RateLimit-*` headers; NVIDIA publishes no per-model limits
and reportedly does not grant increases. The documented remedy is client-side
failover, which **this app did not have then** (it has since 2026-10-10:
`AGENT_FALLBACK_MODEL`). `WORKHORSE_MODEL` backed five of six
BI stages, so one throttled model stops every job while `DEEP_MODEL` sits idle.

---

# Model choice, 2026-10-10

## Liveness

`models-listed-2026-10-10.json`: `GET /v1/models` listed 80 entries.
`model-liveness-2026-10-10.json`: of 48 general chat candidates, 11 answered a
small JSON request. Most of the rest returned 404; `kimi-k2.6` (listed) was gone,
`nemotron-3-nano-omni` returned 503, `deepseek-v4.1-flash` timed out.
`ising-calibration` and `poolside/laguna` answer but are special-purpose
(quantum calibration, coding) and were not considered.

## The sweep

Every BI stage of all 16 eval cases on one model at a time, critic fixed on
`nvidia/nemotron-3-super-120b-a12b`, `CRITIC_RETRY_THRESHOLD=0`:

```bash
CRITIC_RETRY_THRESHOLD=0 uv run python -m evals.run --model <model> --label model-<slug>
uv run python -m evals.models evals/results/model-*.json
```

Full tables: `tasks/evidence/models-2026-10-10.md`.

| model | ok | quality | median s/stage (with critic) |
|---|---|---|---|
| google/gemma-4-31b-it | 96/96 | 85.5 | 63 |
| nvidia/nemotron-3-super-120b-a12b | 96/96 | 82.3 | 32 |
| moonshotai/kimi-k3 | 96/96 | 79.1 | 67 |
| nvidia/nemotron-3-ultra-550b-a55b | 95/96 | 77.3 | 87 |
| openai/gpt-oss-20b (previous) | 84/96 | 68.3 | 44 |

Dropped part-way: DeepSeek V4.1 Flash (504 on every call), GLM 5.3 (50-215 s a
stage, ran out of tokens mid-JSON).

Market research suite, `--repeat 3`: Gemma 85.8 against gpt-oss-20b 62.0.

## Re-runs are worth their time

Gemma with re-runs below 60 against re-runs off (`retry60-gemma.md`): market
critic 56.9 -> 70.1, competitive 55.2 -> 70.6, SWOT 62.9 -> 69.9, every score
under 40 gone but one, for about 30 s more per re-run stage. The threshold stays
at 60.

## Before reading these numbers against older ones

The critic used to see 4,000 characters of the page; analysts see up to 15,000.
It scored correct facts from further down as fabricated - Kimi K3's Valcrest
profile went from 52 to 95 once it could see them. Every confidence number here
is critic/2, with the full window; older ones are not comparable.

`model-google-gemma-4-31b-it.json` is the run `evals/baseline.json` was recorded
from.
