# Eval results

Cases: 7 · stages: profile, market, competitive, swot, marketing, report

## Contract score

Deterministic: completeness, less 15 per failed case assertion.

| Case | profile | market | competitive | swot | marketing | report | mean |
|---|---|---|---|---|---|---|---|
| stripe | 100 | 100 | 100 | 100 | 100 | 100 | 100.0 |
| ghost | 100 | 100 | 100 | 100 | 100 | 33 | 88.9 |
| eleventy | 100 | 100 | 100 | 100 | 100 | 100 | 100.0 |
| linear | 100 | 100 | 100 | 25 | 100 | 33 | 76.4 |
| basecamp | 100 | 100 | 100 | 100 | 100 | **fail** | 83.3 |
| flyio | 100 | 100 | 100 | 100 | 100 | 100 | 100.0 |
| govuk | 100 | 100 | 100 | 100 | 100 | **fail** | 83.3 |

**Overall: 90.3**

`!` marks a failed case assertion.

## Critic confidence

LLM review, 0-100. Noisy - read the spread, not a single cell.

| Case | profile | market | competitive | swot | marketing | report | mean |
|---|---|---|---|---|---|---|---|
| stripe | 95 | – | 78 | 58 | 78 | – | 77.2 |
| ghost | 95 | 55 | 78 | 35 | 78 | – | 68.2 |
| eleventy | 93 | 45 | 58 | 78 | 48 | – | 64.4 |
| linear | 85 | 45 | – | – | – | – | 65.0 |
| basecamp | 95 | 92 | 78 | 55 | – | – | 80.0 |
| flyio | 95 | 45 | 62 | 58 | 93 | – | 70.6 |
| govuk | – | 58 | 45 | 45 | 78 | – | 56.5 |

## Metric health

- **completeness**: 40 stages, 3 distinct values, mean 94.8
- **confidence**: 29 stages, 11 distinct values, mean 69.0

Mean confidence by stage, worst first:

- `swot` 54.8 (n=6)
- `market` 56.7 (n=6)
- `competitive` 66.5 (n=6)
- `marketing` 75.0 (n=5)
- `profile` 93.0 (n=6)

## Findings

**ghost · report**
- contract: recommendations has 1 items, expected at least 5
- contract: risk_factors has 1 items, expected at least 3

**linear · swot**
- contract: weaknesses has 0 items, expected at least 5
- contract: opportunities has 0 items, expected at least 5
- contract: threats has 0 items, expected at least 5

**linear · report**
- contract: recommendations has 1 items, expected at least 5
- contract: risk_factors has 1 items, expected at least 3

**basecamp · report**
- failed: BIReport could not be built: invalid JSON: Expecting ',' delimiter: line 1 column 10629 (char 10628)

**govuk · report**
- failed: BIReport could not be built: invalid JSON: Expecting ',' delimiter: line 1 column 12883 (char 12882)
