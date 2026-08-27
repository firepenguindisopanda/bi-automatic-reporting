# Eval results

Cases: 7 · stages: profile, market, competitive, swot, marketing, report

## Contract score

Deterministic: completeness, less 15 per failed case assertion.

| Case | profile | market | competitive | swot | marketing | report | mean |
|---|---|---|---|---|---|---|---|
| stripe | 100 | 100 | 100 | 100 | 88 | 100 | 97.9 |
| ghost | 100 | 100 | 100 | 25 | 100 | 100 | 87.5 |
| eleventy | 83 | 100 | 100 | 100 | 100 | 33 | 86.1 |
| linear | 100 | 100 | 100 | 100 | 100 | 100 | 100.0 |
| basecamp | 100 | 100 | 100 | 100 | 100 | 100 | 100.0 |
| flyio | 100 | 100 | 50 | 100 | 100 | 100 | 91.7 |
| govuk | 100 | 100 | 100 | 100 | 100 | 100 | 100.0 |

**Overall: 94.7**

`!` marks a failed case assertion.

## Critic confidence

LLM review, 0-100. Noisy - read the spread, not a single cell.

| Case | profile | market | competitive | swot | marketing | report | mean |
|---|---|---|---|---|---|---|---|
| stripe | 95 | 60 | 72 | 45 | 58 | – | 66.0 |
| ghost | 82 | 58 | 80 | 93 | 88 | – | 80.2 |
| eleventy | 92 | 45 | 68 | 58 | 42 | – | 61.0 |
| linear | 95 | – | 58 | 45 | 45 | – | 60.8 |
| basecamp | 95 | 55 | 78 | 55 | 62 | – | 69.0 |
| flyio | 93 | 45 | 78 | – | 78 | – | 73.5 |
| govuk | 95 | 58 | 35 | 45 | 92 | – | 65.0 |

## Metric health

- **completeness**: 42 stages, 6 distinct values, mean 94.7
- **confidence**: 33 stages, 16 distinct values, mean 68.0

Mean confidence by stage, worst first:

- `market` 53.5 (n=6)
- `swot` 56.8 (n=6)
- `marketing` 66.4 (n=7)
- `competitive` 67.0 (n=7)
- `profile` 92.4 (n=7)

## Findings

**stripe · marketing**
- contract: personas has 1 items, expected at least 2

**ghost · swot**
- contract: weaknesses has 0 items, expected at least 5
- contract: opportunities has 0 items, expected at least 5
- contract: threats has 0 items, expected at least 5

**eleventy · profile**
- contract: offerings has 2 items, expected at least 3

**eleventy · report**
- contract: recommendations has 1 items, expected at least 5
- contract: risk_factors has 1 items, expected at least 3

**flyio · competitive**
- contract: direct_competitors has 0 items, expected at least 3
- contract: indirect_competitors has 0 items, expected at least 2
- contract: direct_competitors is empty
- contract: market_gaps has 0 items, expected at least 2
