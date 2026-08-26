"""Tests for schema coercion at the model boundary.

The agent runner hands raw model output to Pydantic, so anything the model
reliably returns that the schema refuses is a stage failure with no recovery -
the repair turn cannot argue the model out of following its own prompt.
"""

import json

import pytest

from app.llm.client import LLMClient
from app.models.bi import MarketAnalysis, MarketSynthesis


class TestMarketSizeCoercion:
    """The sizing prompt asks for TAM, SAM and SOM; the field holds one line."""

    def test_tam_sam_som_object_is_flattened(self) -> None:
        m = MarketSynthesis.model_validate(
            {
                "market_size": {
                    "TAM": "$43.6 billion",
                    "SAM": "$23.1 billion",
                    "SOM": "$8.5 billion",
                }
            }
        )
        assert m.market_size == "TAM: $43.6 billion · SAM: $23.1 billion · SOM: $8.5 billion"

    def test_keys_are_normalised(self) -> None:
        m = MarketSynthesis.model_validate({"market_size": {"tam": "$1B"}})
        assert m.market_size == "TAM: $1B"

    def test_empty_figures_are_dropped(self) -> None:
        m = MarketSynthesis.model_validate({"market_size": {"TAM": "$1B", "SAM": "", "SOM": None}})
        assert m.market_size == "TAM: $1B"

    def test_empty_object_scores_as_missing_rather_than_failing(self) -> None:
        """An empty answer is a low contract score, not a dead pipeline."""
        assert MarketSynthesis.model_validate({"market_size": {}}).market_size == ""

    def test_a_plain_string_is_left_alone(self) -> None:
        text = "$43.6B TAM growing at 24.5% CAGR"
        assert MarketSynthesis.model_validate({"market_size": text}).market_size == text

    def test_the_bi_market_stage_gets_the_same_tolerance(self) -> None:
        m = MarketAnalysis.model_validate(
            {
                "target_market": "Developers",
                "market_size": {"TAM": "$43.6B"},
                "pricing_strategy": "Freemium",
                "market_positioning": "Open source",
            }
        )
        assert m.market_size == "TAM: $43.6B"


class TestSizingStageRegression:
    """The exact payload that failed three attempts and stopped the pipeline.

    Captured from a live run: `market_size: Input should be a valid string`.
    """

    PAYLOAD = {
        "executive_summary": "The developer tools market is expanding quickly.",
        "summary": "A long description of the market landscape.",
        "market_size": {
            "TAM": "$43.6 billion (estimated, based on industry reports)",
            "SAM": "$23.1 billion (estimated, based on market share analysis)",
            "SOM": "$8.5 billion (estimated, based on realistic capture in 1-3 years)",
        },
        "growth_rate": "24.5% CAGR (estimated)",
        "key_insights": ["a", "b", "c"],
        "recommendations": ["a", "b", "c"],
        "risks_and_assumptions": ["a", "b"],
        "swot": {"strengths": [], "weaknesses": [], "opportunities": [], "threats": []},
    }

    def test_the_payload_now_validates(self) -> None:
        m = MarketSynthesis.model_validate(self.PAYLOAD)
        assert m.market_size.startswith("TAM: $43.6 billion")
        assert "SOM: $8.5 billion" in m.market_size

    def test_the_runner_parses_it_out_of_a_raw_reply(self) -> None:
        """End to end through the parser the agent runner actually calls."""
        diagnosis: list[str] = []
        parsed = LLMClient(model="test-model").parse_json_response(
            f"```json\n{json.dumps(self.PAYLOAD)}\n```", MarketSynthesis, diagnosis
        )
        assert parsed is not None, f"still unparseable: {diagnosis}"
        assert parsed.growth_rate == "24.5% CAGR (estimated)"

    @pytest.mark.parametrize("field", ["executive_summary", "summary", "growth_rate"])
    def test_neighbouring_text_fields_are_untouched(self, field: str) -> None:
        m = MarketSynthesis.model_validate(self.PAYLOAD)
        assert getattr(m, field) == self.PAYLOAD[field]
