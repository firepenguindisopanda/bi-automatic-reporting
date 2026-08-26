# Eval results

Cases: 7 · stages: profile, market, competitive, swot, marketing, report

## Contract score

Deterministic: completeness, less 15 per failed case assertion.

| Case | profile | market | competitive | swot | marketing | report | mean |
|---|---|---|---|---|---|---|---|
| stripe | 100 | 100 | 100 | 100 | 100 | 100 | 100.0 |
| ghost | 100 | 100 | 100 | 100 | 100 | 100 | 100.0 |
| eleventy | 100 | 100 | 100 | 100 | 100 | 33 | 88.9 |
| linear | 100 | 100 | 88 | 100 | 100 | 67 | 92.4 |
| basecamp | 100 | 100 | 100 | 100 | 100 | **fail** | 83.3 |
| flyio | 100 | 100 | 100 | 100 | 100 | 100 | 100.0 |
| govuk | 100 | 100 | 100 | 100 | 100 | **fail** | 83.3 |

**Overall: 92.6**

`!` marks a failed case assertion.

## Critic confidence

LLM review, 0-100. Noisy - read the spread, not a single cell.

| Case | profile | market | competitive | swot | marketing | report | mean |
|---|---|---|---|---|---|---|---|
| stripe | 78 | 62 | 62 | – | 45 | – | 61.8 |
| ghost | 95 | 58 | 93 | 58 | 55 | – | 71.8 |
| eleventy | 30 | 55 | 78 | 78 | 42 | – | 56.6 |
| linear | 80 | 38 | 78 | 38 | 55 | – | 57.8 |
| basecamp | 95 | 92 | 75 | – | 62 | – | 81.0 |
| flyio | 95 | 95 | – | 78 | 78 | – | 86.5 |
| govuk | 88 | 55 | 78 | 55 | 92 | – | 73.6 |

## Metric health

- **completeness**: 40 stages, 4 distinct values, mean 97.2
- **confidence**: 32 stages, 14 distinct values, mean 69.2

Mean confidence by stage, worst first:

- `marketing` 61.3 (n=7)
- `swot` 61.4 (n=5)
- `market` 65.0 (n=7)
- `competitive` 77.3 (n=6)
- `profile` 80.1 (n=7)

## Findings

**eleventy · report**
- contract: recommendations has 1 items, expected at least 5
- contract: risk_factors has 1 items, expected at least 3

**linear · competitive**
- contract: market_gaps has 0 items, expected at least 2

**linear · report**
- contract: risk_factors has 0 items, expected at least 3

**basecamp · report**
- failed: BIReport could not be built: invalid JSON: Expecting ',' delimiter: line 1 column 7926 (char 7925)

**govuk · report**
- failed: BIReport could not be built: invalid JSON: Expecting ',' delimiter: line 1 column 8580 (char 8579)
