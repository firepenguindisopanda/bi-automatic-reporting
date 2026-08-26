"""Tests for deterministic stage scoring."""

import json
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from app.analysis.specs import (
    COMPETITIVE_CONTRACT,
    MARKETING_CONTRACT,
    PROFILE_CONTRACT,
    REPORT_CONTRACT,
    RESOURCES_CONTRACT,
    SWOT_CONTRACT,
)
from app.models.bi import (
    BusinessProfile,
    CompetitiveAnalysis,
    MarketResources,
    ScrapedContent,
    SWOTAnalysis,
)
from app.pipeline.contracts import (
    FIELD_ECHO_THRESHOLD,
    NEAR_IDENTICAL_THRESHOLD,
    StageContract,
    field_differs_from,
    find_echoed_examples,
    find_placeholders,
    grounded_in_source,
    items_have,
    min_items,
    min_length,
    no_cross_list_duplicates,
    no_duplicate_names,
    no_near_identical_items,
    required,
    similarity,
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


# --- M4: rules that can actually fail --------------------------------------
#
# The resources output that shipped the CircleCI error scored 100.0 while
# carrying 18 distinct names across 21 entries, one description reused
# verbatim, and relevance fields that restated those descriptions. Every rule
# below exists because none of the existing ones noticed any of that.


class Resources(BaseModel):
    """The shape of the stage these rules were written for."""

    key_players: list[dict] = Field(default_factory=list)
    competitor_sites: list[dict] = Field(default_factory=list)
    news_sources: list[dict] = Field(default_factory=list)


def _entry(name: str, description: str = "", relevance: str = "") -> dict:
    return {"name": name, "url": f"https://{name.lower()}.example", 
            "description": description, "relevance": relevance}


class TestSimilarity:
    def test_identical_text_scores_one(self) -> None:
        assert similarity("a self-hosted tool", "a self-hosted tool") == 1.0

    def test_punctuation_and_case_are_ignored(self) -> None:
        assert similarity("Self-Hosted CI/CD!", "self hosted ci cd") == 1.0

    def test_unrelated_text_scores_low(self) -> None:
        assert similarity("payments infrastructure", "static site generator") < 0.3

    def test_empty_text_scores_zero_rather_than_raising(self) -> None:
        assert similarity("", "anything") == 0.0
        assert similarity("", "") == 0.0

    def test_a_name_swap_still_reads_as_near_identical(self) -> None:
        """The exact shape of the templated output this rule set targets."""
        a = "Jenkins is a self-hosted CI/CD tool that offers flexibility and scalability."
        b = "GoCD is a self-hosted CI/CD tool that offers flexibility and scalability."
        assert similarity(a, b) >= NEAR_IDENTICAL_THRESHOLD


class TestNoCrossListDuplicates:
    rule = staticmethod(no_cross_list_duplicates(["key_players", "competitor_sites"]))

    def test_passes_when_the_lists_are_distinct(self) -> None:
        value = Resources(
            key_players=[_entry("Jenkins")], competitor_sites=[_entry("Drone")]
        )
        assert self.rule(value) is None

    def test_fails_when_an_entity_appears_in_both(self) -> None:
        value = Resources(
            key_players=[_entry("GitLab")], competitor_sites=[_entry("GitLab")]
        )
        warning = self.rule(value)
        assert warning is not None
        assert "GitLab" in warning

    def test_matches_case_insensitively(self) -> None:
        value = Resources(
            key_players=[_entry("GitLab")], competitor_sites=[{"name": "gitlab  "}]
        )
        assert self.rule(value) is not None

    def test_a_repeat_inside_one_list_is_left_to_no_duplicate_names(self) -> None:
        """Otherwise the same defect is penalised twice by two rules."""
        value = Resources(key_players=[_entry("Jenkins"), _entry("Jenkins")])
        assert self.rule(value) is None
        assert no_duplicate_names("key_players")(value) is not None

    def test_empty_input_passes(self) -> None:
        assert self.rule(Resources()) is None

    def test_entries_without_the_key_are_skipped(self) -> None:
        value = Resources(key_players=[{"url": "x"}], competitor_sites=[{"url": "y"}])
        assert self.rule(value) is None


class TestFieldDiffersFrom:
    rule = staticmethod(field_differs_from("key_players", "description", "relevance"))

    def test_passes_when_relevance_adds_something(self) -> None:
        value = Resources(
            key_players=[
                _entry(
                    "Drone",
                    description="A container-native pipeline engine configured in YAML.",
                    relevance="Ships as a single Go binary, so a two-person team can run it.",
                )
            ]
        )
        assert self.rule(value) is None

    def test_fails_when_relevance_restates_the_description(self) -> None:
        text = "CircleCI is a self-hosted CI/CD tool for small engineering teams."
        value = Resources(key_players=[_entry("CircleCI", description=text, relevance=text)])
        warning = self.rule(value)
        assert warning is not None
        assert "CircleCI" in warning

    def test_an_empty_field_is_left_to_items_have(self) -> None:
        value = Resources(key_players=[_entry("Drone", description="Something.", relevance="")])
        assert self.rule(value) is None

    def test_empty_input_passes(self) -> None:
        assert self.rule(Resources()) is None

    def test_the_threshold_is_the_boundary(self) -> None:
        assert FIELD_ECHO_THRESHOLD == 0.60


class TestNoNearIdenticalItems:
    rule = staticmethod(no_near_identical_items("key_players"))

    def test_passes_when_descriptions_are_genuinely_different(self) -> None:
        value = Resources(
            key_players=[
                _entry("Drone", description="A container-native pipeline engine."),
                _entry("Jenkins", description="A long-lived Java automation server with plugins."),
            ]
        )
        assert self.rule(value) is None

    def test_fails_when_entries_share_a_templated_sentence(self) -> None:
        """The real defect: six products, one sentence, the name swapped."""
        template = "{} is a self-hosted CI/CD tool that offers flexibility and scalability."
        value = Resources(
            key_players=[
                _entry(n, description=template.format(n))
                for n in ("CircleCI", "GitLab", "Jenkins", "Codeship")
            ]
        )
        warning = self.rule(value)
        assert warning is not None
        assert "pair(s)" in warning

    def test_a_single_entry_cannot_be_near_identical_to_anything(self) -> None:
        value = Resources(key_players=[_entry("Drone", description="Anything at all.")])
        assert self.rule(value) is None

    def test_empty_input_passes(self) -> None:
        assert self.rule(Resources()) is None

    def test_entries_without_the_field_are_skipped(self) -> None:
        value = Resources(key_players=[{"name": "a"}, {"name": "b"}])
        assert self.rule(value) is None

    def test_the_threshold_is_the_boundary(self) -> None:
        assert NEAR_IDENTICAL_THRESHOLD == 0.85


class TestRulesNeverRaise:
    """Contract scoring runs on every stage; a rule that raises kills the job."""

    @pytest.mark.parametrize(
        "rule",
        [
            no_cross_list_duplicates(["key_players", "missing_path"]),
            field_differs_from("missing_path", "description", "relevance"),
            no_near_identical_items("missing_path"),
        ],
    )
    def test_a_missing_path_is_not_an_error(self, rule) -> None:
        assert rule(Resources()) is None

    @pytest.mark.parametrize(
        "rule",
        [
            no_cross_list_duplicates(["key_players"]),
            field_differs_from("key_players", "description", "relevance"),
            no_near_identical_items("key_players"),
        ],
    )
    def test_ragged_entries_are_not_an_error(self, rule) -> None:
        value = Resources(key_players=[{}, {"name": None}, {"description": 5}])
        assert rule(value) is None or isinstance(rule(value), str)


# --- M4's actual criterion -------------------------------------------------

KNOWN_BAD = (
    Path(__file__).resolve().parents[1]
    / "evals" / "fixtures" / "known_bad" / "resources_self_hosted_cicd.json"
)


def _known_bad() -> MarketResources:
    doc = json.loads(KNOWN_BAD.read_text())
    return MarketResources(**{k: v for k, v in doc.items() if not k.startswith("_")})


class TestKnownBadResources:
    """The output that shipped CircleCI and Travis CI as self-hosted.

    SPEC.md section 8 rule 1: a check that passes on known-broken input proves
    nothing. This is the test that decides whether M4 was worth doing.
    """

    def test_the_fixture_still_matches_the_figures_it_was_built_against(self) -> None:
        """Guards the fixture itself. Derived independently, recorded in SPEC M4."""
        value = _known_bad()
        lists = [
            value.key_players, value.competitor_sites, value.industry_websites,
            value.news_sources, value.research_papers, value.relevant_communities,
        ]
        entries = [e for lst in lists for e in lst]
        assert len(entries) == 21
        assert len({e.name for e in entries}) == 18
        assert len([lst for lst in lists if lst]) == 6

    def test_it_still_carries_the_error_that_started_this(self) -> None:
        """Hosted SaaS listed as self-hosted. No rule catches this - M7 does."""
        names = {e.name for e in _known_bad().key_players}
        assert {"CircleCI", "Travis CI"} <= names

    def test_the_old_rules_scored_it_a_perfect_hundred(self) -> None:
        """Why M4 exists: every pre-M4 rule passes on this output."""
        pre_m4 = StageContract(
            rules=(
                min_items("key_players", 4),
                items_have("key_players", ("name", "url")),
                no_duplicate_names("key_players"),
                min_items("news_sources", 2),
                min_items("industry_websites", 2),
                min_items("competitor_sites", 2),
            ),
        )
        assert pre_m4.evaluate(_known_bad()).completeness == 100.0

    def test_the_current_contract_scores_it_below_a_hundred(self) -> None:
        result = RESOURCES_CONTRACT.evaluate(_known_bad())
        assert result.completeness < 100.0

    def test_every_new_rule_contributes_a_named_warning(self) -> None:
        """A low score is only useful if it says what is wrong."""
        warnings = " ".join(RESOURCES_CONTRACT.evaluate(_known_bad()).warnings)
        assert "repeat the same entity" in warnings
        assert "restates description" in warnings
        assert "near-identical description" in warnings
        # and it names the offending entity, not just the count
        assert "CircleCI" in warnings

    def test_good_output_of_the_same_shape_still_scores_a_hundred(self) -> None:
        """The rules must discriminate, not just fire on everything."""
        def res(name: str, desc: str, rel: str) -> dict:
            return {"name": name, "url": f"https://{name.lower()}.example",
                    "description": desc, "relevance": rel}

        clean = MarketResources(
            key_players=[
                res("Drone", "A container-native pipeline engine configured entirely in YAML.",
                    "Runs as one Go binary; a two-person team can self-host it in an afternoon."),
                res("Woodpecker", "A community fork of Drone kept under permissive licensing.",
                    "Matters here because licensing changes drove teams off the original."),
                res("Concourse", "A pipeline engine built around immutable containers and resources.",
                    "Steeper to learn, but its model prevents the snowflake-agent problem."),
                res("Jenkins", "A long-lived Java automation server with a vast plugin ecosystem.",
                    "The incumbent; relevant as the baseline teams are usually migrating from."),
            ],
            news_sources=[res("LWN", "Long-form Linux and open-source reporting.",
                              "Covers licensing shifts that reshape this category."),
                          res("The New Stack", "Trade coverage of container and CI tooling.",
                              "Tracks vendor funding and acquisition, which predicts churn.")],
            industry_websites=[res("CNCF", "Foundation hosting cloud-native infrastructure projects.",
                                   "Graduation status is a durability signal for a self-hosted tool."),
                               res("OSI", "Steward of the open-source definition.",
                                   "Its licence list decides what counts as genuinely self-hostable.")],
            competitor_sites=[res("Gitea", "A lightweight self-hosted git forge with built-in actions.",
                                  "Bundles CI, so it removes the need for a separate tool entirely."),
                              res("Forgejo", "A community-governed fork of Gitea.",
                                  "Relevant to teams wary of a single-vendor forge.")],
        )
        assert RESOURCES_CONTRACT.evaluate(clean).completeness == 100.0


class TestCompetitorCrossListRule:
    """A company cannot be both a direct and an indirect competitor."""

    def test_distinct_lists_pass(self) -> None:
        value = CompetitiveAnalysis(
            direct_competitors=[{"name": "Stripe"}, {"name": "Adyen"}, {"name": "Braintree"}],
            indirect_competitors=[{"name": "PayPal"}, {"name": "Wise"}],
            competitive_advantages=["a", "b"], market_gaps=["c", "d"],
            threat_assessment="x" * 130,
        )
        assert COMPETITIVE_CONTRACT.evaluate(value).completeness == 100.0

    def test_an_entity_in_both_lists_costs_score(self) -> None:
        value = CompetitiveAnalysis(
            direct_competitors=[{"name": "Stripe"}, {"name": "Adyen"}, {"name": "PayPal"}],
            indirect_competitors=[{"name": "PayPal"}, {"name": "Wise"}],
            competitive_advantages=["a", "b"], market_gaps=["c", "d"],
            threat_assessment="x" * 130,
        )
        result = COMPETITIVE_CONTRACT.evaluate(value)
        assert result.completeness < 100.0
        assert any("PayPal" in w for w in result.warnings)
