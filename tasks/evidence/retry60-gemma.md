# Eval results

Cases: 16 · stages: profile, market, competitive, swot

## Contract score

Deterministic: completeness, less 15 per failed case assertion.

| Case | profile | market | competitive | swot | mean |
|---|---|---|---|---|---|
| stripe | 100 | 100 | 100 | 100 | 100.0 |
| ghost | 100 | 100 | 100 | 100 | 100.0 |
| eleventy | 100 | 86 | 100 | 100 | 96.4 |
| linear | 100 | 86 | 100 | 100 | 96.4 |
| basecamp | 100 | 100 | 100 | 100 | 100.0 |
| flyio | 100 | 100 | 100 | 100 | 100.0 |
| govuk | 100 | 100 | 100 | 100 | 100.0 |
| valcrest | 100 | 100 | 93 | 100 | 98.2 |
| riomed | 100 | 100 | 100 | 100 | 100.0 |
| asawright | 100 | 100 | 100 | 100 | 100.0 |
| angostura | 100 | 100 | 100 | 100 | 100.0 |
| tormach | 100 | 100 | 100 | 100 | 100.0 |
| pilot | 100 | 86 | 100 | 100 | 96.4 |
| tartine | 100 | 100 | 100 | 100 | 100.0 |
| harvestright | 100 | 100 | 93 | 100 | 98.2 |
| allbirds | 100 | 86 | 100 | 100 | 96.4 |

**Overall: 98.9**

`!` marks a failed case assertion.

## Critic confidence

LLM review, 0-100. Noisy - read the spread, not a single cell.

| Case | profile | market | competitive | swot | mean |
|---|---|---|---|---|---|
| stripe | 95 | 62 | 82 | 55 | 73.5 |
| ghost | 95 | 80 | 78 | 82 | 83.8 |
| eleventy | 95 | 95 | 73 | 82 | 86.2 |
| linear | 93 | 93 | 93 | 78 | 89.2 |
| basecamp | 95 | 95 | 82 | 55 | 81.8 |
| flyio | 95 | 78 | 82 | 68 | 80.8 |
| govuk | 95 | 45 | 88 | 55 | 70.8 |
| valcrest | 95 | 68 | 68 | 93 | 81.0 |
| riomed | 95 | 45 | 65 | 78 | 70.8 |
| asawright | 95 | 45 | 55 | 35 | 57.5 |
| angostura | 75 | 55 | 78 | 78 | 71.5 |
| tormach | 95 | 78 | 78 | 78 | 82.2 |
| pilot | 95 | 75 | 62 | 93 | 81.2 |
| tartine | 93 | 58 | 55 | 45 | 62.8 |
| harvestright | 88 | 58 | 45 | 82 | 68.2 |
| allbirds | 92 | 92 | 45 | 62 | 72.8 |

## Metric health

- **completeness**: 64 stages, 3 distinct values, mean 98.9
- **confidence**: 64 stages, 16 distinct values, mean 75.9

Mean confidence by stage, worst first:

- `swot` 69.9 (n=16)
- `market` 70.1 (n=16)
- `competitive` 70.6 (n=16)
- `profile` 92.9 (n=16)

## Findings

**eleventy · market**
- contract: market_size 'Market size not specified in source material' is not a range; the prompt asks for one

**linear · market**
- contract: market_size 'Market size not disclosed in provided source mater' is not a range; the prompt asks for one

**valcrest · competitive**
- contract: direct_competitors has 2 items, expected at least 3

**pilot · market**
- contract: market_size 'Not disclosed in provided source material (Estimat' is not a range; the prompt asks for one

**harvestright · competitive**
- contract: direct_competitors has 2 items, expected at least 3

**allbirds · market**
- contract: market_size 'Estimate unavailable; source data does not provide' is not a range; the prompt asks for one
