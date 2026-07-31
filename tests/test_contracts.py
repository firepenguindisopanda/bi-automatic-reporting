"""Tests for deterministic stage scoring."""

import pytest
from pydantic import BaseModel, Field

from app.analysis.specs import (
    COMPETITIVE_CONTRACT,
    MARKETING_CONTRACT,
    PROFILE_CONTRACT,
    REPORT_CONTRACT,
    SWOT_CONTRACT,
)
from app.models.bi import BusinessProfile, ScrapedContent, SWOTAnalysis
from app.pipeline.contracts import (
    StageContract,
    find_echoed_examples,
    find_placeholders,
    grounded_in_source,
    items_have,
    min_items,
    min_length,
    no_duplicate_names,
    required,
)


class Sample(BaseModel):
    name: str = ""
    items: list[str] = Field(default_factory=list)
    entries: list[dict] = Field(default_factory=list)
    body: str = ""


class TestRules:
    def test_required_flags_empty(self) -> None:
        assert required("name")(Sample()) is not None
        assert required("name")(Sample(name="Acme")) is None

    def test_required_flags_empty_list(self) -> None:
        assert required("items")(Sample()) is not None

    def test_min_items_counts(self) -> None:
        assert min_items("items", 3)(Sample(items=["a", "b"])) is not None
        assert min_items("items", 3)(Sample(items=["a", "b", "c"])) is None

    def test_min_length_ignores_whitespace(self) -> None:
        assert min_length("body", 10)(Sample(body="   hi   ")) is not None
        assert min_length("body", 10)(Sample(body="x" * 10)) is None

    def test_items_have_required_keys(self) -> None:
        rule = items_have("entries", ("name",))
        assert rule(Sample(entries=[{"name": "A"}, {"other": "B"}])) is not None
        assert rule(Sample(entries=[{"name": "A"}, {"name": "B"}])) is None

    def test_no_duplicate_names(self) -> None:
        rule = no_duplicate_names("entries")
        assert rule(Sample(entries=[{"name": "A"}, {"name": "a "}])) is not None
        assert rule(Sample(entries=[{"name": "A"}, {"name": "B"}])) is None

    def test_dotted_paths(self) -> None:
        assert min_items("swot.strengths", 2)(_swot(1)) is not None
        assert min_items("swot.strengths", 2)(_swot(3)) is None


class Wrapper(BaseModel):
    swot: SWOTAnalysis


def _swot(n: int) -> Wrapper:
    return Wrapper(
        swot=SWOTAnalysis(
            strengths=[f"s{i}" for i in range(n)], weaknesses=[], opportunities=[], threats=[]
        )
    )


class TestPlaceholders:
    @pytest.mark.parametrize("text", ["<string>", "N/A", "...", "TODO", "not applicable"])
    def test_detects_placeholder_values(self, text: str) -> None:
        assert find_placeholders(Sample(name=text))

    def test_allows_the_phrase_inside_a_real_sentence(self) -> None:
        """Short markers are matched exactly, so prose is not punished."""
        assert not find_placeholders(
            Sample(body="Regulation is not applicable to their current market.")
        )

    def test_angle_bracket_markers_match_anywhere(self) -> None:
        assert find_placeholders(Sample(body="The value is <string> here"))

    def test_searches_nested_structures(self) -> None:
        assert find_placeholders(Sample(entries=[{"name": "<string>"}]))


class TestEchoedExamples:
    """The eval harness found Eleventy classified as the prompt's own example."""

    def test_detects_prompt_example_echo(self) -> None:
        hits = find_echoed_examples(Sample(name="SaaS E-commerce Analytics"))
        assert hits
        assert "copied the prompt" in hits[0]

    def test_case_and_space_insensitive(self) -> None:
        assert find_echoed_examples(Sample(name="  saas e-commerce analytics  "))

    def test_real_answers_are_untouched(self) -> None:
        assert not find_echoed_examples(Sample(name="Static site generation tooling"))

    def test_scores_below_a_clean_answer(self) -> None:
        contract = StageContract(rules=(required("name"),))
        echoed = contract.evaluate(Sample(name="SaaS E-commerce Analytics"))
        clean = contract.evaluate(Sample(name="Static site generation tooling"))
        assert echoed.completeness < clean.completeness
        assert clean.completeness == 100.0


class TestGrounding:
    def _ctx(self, text: str):
        class Ctx:
            scraped = ScrapedContent(
                url="https://acme.example.com", title="", description="", text_content=text
            )

        return Ctx()

    def test_accepts_a_name_present_on_the_page(self) -> None:
        rule = grounded_in_source("name")
        assert rule(Sample(name="Acme"), self._ctx("Welcome to Acme Analytics")) is None

    def test_accepts_a_partial_match(self) -> None:
        rule = grounded_in_source("name")
        assert rule(Sample(name="Acme Corporation Ltd"), self._ctx("Acme builds things")) is None

    def test_flags_a_name_that_is_nowhere_on_the_page(self) -> None:
        rule = grounded_in_source("name")
        assert rule(Sample(name="Globex"), self._ctx("Acme builds things")) is not None

    def test_empty_value_is_left_to_the_required_rule(self) -> None:
        assert grounded_in_source("name")(Sample(), self._ctx("anything")) is None

    def test_no_context_means_no_opinion(self) -> None:
        assert grounded_in_source("name")(Sample(name="Globex"), None) is None


class TestScoring:
    def test_all_rules_passing_scores_full(self) -> None:
        contract = StageContract(rules=(required("name"), min_items("items", 1)))
        assert contract.evaluate(Sample(name="Acme", items=["x"])).completeness == 100.0

    def test_half_the_rules_failing_scores_half(self) -> None:
        contract = StageContract(rules=(required("name"), min_items("items", 1)))
        assert contract.evaluate(Sample(name="Acme")).completeness == 50.0

    def test_never_negative(self) -> None:
        contract = StageContract(rules=(required("name"),))
        assert contract.evaluate(Sample(name="<string>")).completeness >= 0.0

    def test_context_rules_count_toward_the_total(self) -> None:
        contract = StageContract(
            rules=(required("name"),), context_rules=(grounded_in_source("name"),)
        )

        class Ctx:
            scraped = ScrapedContent(url="", title="", description="", text_content="nothing here")

        # Present but ungrounded: one of two checks fails.
        assert contract.evaluate(Sample(name="Globex"), Ctx()).completeness == 50.0

    def test_duplicates_are_penalised(self) -> None:
        result = StageContract().evaluate(Sample(items=["same", "SAME "]))
        assert result.completeness < 100.0
        assert any("duplicate" in w for w in result.warnings)


class TestRealContracts:
    """The shipped contracts must actually reward good output and punish thin output."""

    def test_full_swot_scores_high(self) -> None:
        full = SWOTAnalysis(
            strengths=[f"s{i}" for i in range(5)],
            weaknesses=[f"w{i}" for i in range(5)],
            opportunities=[f"o{i}" for i in range(5)],
            threats=[f"t{i}" for i in range(5)],
        )
        assert SWOT_CONTRACT.evaluate(full).completeness == 100.0

    def test_thin_swot_scores_zero(self) -> None:
        thin = SWOTAnalysis(strengths=["a"], weaknesses=["b"], opportunities=["c"], threats=["d"])
        result = SWOT_CONTRACT.evaluate(thin)
        assert result.completeness == 0.0
        assert len(result.warnings) == 4

    def test_profile_contract_checks_grounding(self) -> None:
        profile = BusinessProfile(
            company_name="Globex", tagline="t", description="d", industry="SaaS",
            offerings=["a", "b", "c"], target_audience=["x"], value_proposition="v",
        )

        class Ctx:
            scraped = ScrapedContent(
                url="https://acme.example.com", title="Acme", description="",
                text_content="Acme builds analytics dashboards",
            )

        result = PROFILE_CONTRACT.evaluate(profile, Ctx())
        assert result.completeness < 100.0
        assert any("does not appear" in w for w in result.warnings)

    @pytest.mark.parametrize(
        "contract",
        [PROFILE_CONTRACT, COMPETITIVE_CONTRACT, SWOT_CONTRACT, MARKETING_CONTRACT, REPORT_CONTRACT],
    )
    def test_every_contract_declares_rules(self, contract: StageContract) -> None:
        assert contract.rules or contract.context_rules
