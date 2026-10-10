"""Stage-targeted eval assertions, and the fixtures' own assertions.

Offline: assertions are evaluated against hand-written outputs, the saved
fixture pages and the stored known-bad outputs in evals/fixtures/known_bad/.
No model is called.
"""

import json
import re
from pathlib import Path

import pytest

from app.models.bi import BusinessProfile, MarketResources
from evals.assertions import StageAssertion, parse_assertion, parse_assertions, select
from evals.run import BI_SUITE, MARKET_SUITE, check_assertions, load_cases, scrape_fixture

KNOWN_BAD = Path(__file__).resolve().parents[1] / "evals" / "fixtures" / "known_bad"


def assertion(**raw: object) -> StageAssertion:
    return parse_assertion(raw, BI_SUITE.stage_keys + MARKET_SUITE.stage_keys)


def bi_case(name: str) -> dict:
    return next(c for c in load_cases(BI_SUITE) if c["name"] == name)


def failures_for(case: dict, stage: str, value: object) -> list[str]:
    """Only the stage-assertion failures, not the legacy profile checks."""
    return [f for a in parse_assertions(case) if a.applies_to(stage) for f in a.evaluate(stage, value)]


# --- schema validation -----------------------------------------------------


class TestSchema:
    def test_a_minimal_assertion_parses(self):
        a = assertion(stage="profile", forbid=["Acme"])
        assert a.stages == ("profile",)
        assert a.forbid == ("Acme",)

    def test_stages_takes_a_list(self):
        assert assertion(stages=["profile", "report"], forbid="x").stages == ("profile", "report")

    @pytest.mark.parametrize(
        ("raw", "message"),
        [
            ({"stage": "pofile", "forbid": ["x"]}, "unknown stage"),
            ({"stage": "profile", "forbid_regex": ["("]}, "bad regex"),
            ({"stage": "profile"}, "needs at least one of"),
            ({"stage": "profile", "stages": ["market"], "forbid": ["x"]}, "exactly one of"),
            ({"forbid": ["x"]}, "exactly one of"),
            ({"stage": "profile", "forbidden": ["x"]}, "unknown key"),
            ({"stage": "profile", "forbid": []}, "non-empty"),
            ({"stage": "profile", "path": "a..b", "forbid": ["x"]}, "dotted field path"),
        ],
    )
    def test_mistakes_are_rejected_with_a_reason(self, raw, message):
        with pytest.raises(ValueError, match=message):
            parse_assertion(raw, BI_SUITE.stage_keys)

    def test_the_error_names_the_case_and_entry(self):
        case = {"name": "demo", "assertions": [{"stage": "profile", "forbid": ["x"]}, {"stage": "nope", "forbid": "x"}]}
        with pytest.raises(ValueError, match=r"'demo' assertion #2"):
            parse_assertions(case, BI_SUITE.stage_keys)

    def test_a_typo_in_a_case_file_fails_at_load_time(self, tmp_path, monkeypatch):
        """Before a single stage is paid for."""
        import evals.run as run

        (tmp_path / "cases.yaml").write_text(
            "cases:\n  - name: x\n    assertions:\n      - {stage: swott, forbid: [a]}\n", encoding="utf-8"
        )
        monkeypatch.setattr(run, "EVALS_DIR", tmp_path)
        with pytest.raises(ValueError, match="swott"):
            run.load_cases(BI_SUITE)

    @pytest.mark.parametrize("suite", [BI_SUITE, MARKET_SUITE], ids=lambda s: s.name)
    def test_every_shipped_case_validates(self, suite):
        for case in load_cases(suite):
            parse_assertions(case, suite.stage_keys)


# --- evaluation --------------------------------------------------------------


class TestForbid:
    def test_literal_match_is_case_insensitive(self):
        a = assertion(stage="profile", forbid=["Acme Corp"])
        assert a.evaluate("profile", {"company_name": "ACME CORP ltd"}) == [
            "[profile] contains forbidden 'Acme Corp'"
        ]

    def test_each_forbidden_term_is_its_own_failure(self):
        a = assertion(stage="resources", forbid=["CircleCI", "Travis CI", "GitHub Actions"])
        found = a.evaluate("resources", {"names": ["CircleCI", "travis ci", "Jenkins"]})
        assert len(found) == 2

    def test_field_names_never_match(self):
        """`direct_competitors` must not trip a forbidden 'competitor'."""
        a = assertion(stage="competitive", forbid=["competitor"])
        assert a.evaluate("competitive", {"direct_competitors": [{"name": "Hugo"}]}) == []

    def test_regex_match_quotes_the_offending_text(self):
        a = assertion(stage="profile", forbid_regex=[r"free trials?"])
        [failure] = a.evaluate("profile", {"description": "Start a Free Trial today"})
        assert "Free Trial" in failure and "/free trials?/" in failure


class TestRequireAny:
    def test_one_literal_is_enough(self):
        a = assertion(stage="resources", require_any=["Gitea", "Forgejo"])
        assert a.evaluate("resources", {"x": ["Forgejo Actions"]}) == []

    def test_one_regex_is_enough(self):
        a = assertion(stage="market", require_any_regex=[r"open[- ]?source", r"\bfree"])
        assert a.evaluate("market", {"pricing_strategy": "Free to use"}) == []

    def test_literals_and_regexes_combine(self):
        a = assertion(stage="market", require_any=["donation"], require_any_regex=[r"\bfree"])
        assert a.evaluate("market", {"p": "Funded by donations"}) == []

    def test_none_present_is_one_failure_naming_them_all(self):
        a = assertion(stage="market", require_any=["Drone"], require_any_regex=["woodpecker"])
        assert a.evaluate("market", {"p": "Jenkins"}) == ["[market] mentions none of ['Drone', '/woodpecker/']"]


class TestTargeting:
    def test_an_assertion_only_applies_to_the_stages_it_names(self):
        case = {"name": "c", "assertions": [{"stage": "market", "forbid": ["SaaS"]}]}
        assert check_assertions("profile", {"industry": "SaaS"}, case) == []
        assert check_assertions("market", {"target_market": "SaaS buyers"}, case) == [
            "[market] contains forbidden 'SaaS'"
        ]

    def test_a_path_narrows_the_text(self):
        a = assertion(stage="resources", path="key_players.name", forbid=["CircleCI"])
        value = {
            "key_players": [{"name": "Woodpecker", "description": "A self-hosted alternative to CircleCI"}],
            "competitor_sites": [{"name": "CircleCI"}],
        }
        assert a.evaluate("resources", value) == []

    def test_a_path_walks_lists_element_wise(self):
        values, found = select({"a": [{"b": "x"}, {"b": "y"}, {"c": "z"}]}, ["a", "b"])
        assert found and values == ["x", "y"]

    def test_an_empty_list_or_null_is_found_but_empty(self):
        assert select({"a": []}, ["a", "b"]) == ([], True)
        assert select({"a": None}, ["a", "b"]) == ([], True)
        a = assertion(stage="resources", path="key_players", require_any=["Drone"])
        assert a.evaluate("resources", {"key_players": []}) == ["[resources.key_players] mentions none of ['Drone']"]

    def test_a_missing_path_fails_loudly(self):
        """A forbid that silently stopped looking after a model change would pass forever."""
        a = assertion(stage="report", path="market_analysis.market_size", forbid_regex=[r"\$"])
        assert a.evaluate("report", {"executive_summary": "..."}) == [
            "[report.market_analysis.market_size] path not found in the report output"
        ]

    def test_whole_output_text_does_not_depend_on_the_output_shape(self):
        """The report stage is moving from BIReport to ReportSynthesis; a
        whole-output assertion must read both the same way."""
        a = assertion(stage="report", forbid_regex=[r"\$\d+/month"])
        bi_report_shape = {
            "executive_summary": "Summary.",
            "business_profile": {"description": "Pro costs $9/month"},
            "recommendations": ["r"],
        }
        synthesis_shape = {"executive_summary": "Pro costs $9/month", "recommendations": ["r"], "risk_factors": []}
        assert len(a.evaluate("report", bi_report_shape)) == 1
        assert len(a.evaluate("report", synthesis_shape)) == 1

    def test_pydantic_models_and_plain_dicts_read_the_same(self):
        a = assertion(stage="resources", path="key_players.name", forbid=["Travis CI"])
        model = MarketResources(key_players=[{"name": "Travis CI", "url": "https://travis-ci.com", "description": "d"}])
        assert a.evaluate("resources", model) == a.evaluate("resources", model.model_dump()) != []


class TestLegacyProfileChecksStillWork:
    def profile(self, **overrides: object) -> BusinessProfile:
        fields = {
            "company_name": "Stripe",
            "tagline": "Financial infrastructure",
            "description": "Online payment processing for internet businesses.",
            "industry": "Fintech / payment infrastructure",
            "offerings": ["Payments", "Billing", "Connect"],
            "value_proposition": "Accept payments",
        }
        return BusinessProfile(**{**fields, **overrides})

    def test_a_correct_profile_passes(self):
        assert check_assertions("profile", self.profile(), bi_case("stripe")) == []

    def test_each_legacy_check_still_fires(self):
        bad = self.profile(company_name="Acme Corp", industry="Retail", offerings=["x"], description="Shoes.",
                           tagline="t", value_proposition="v")
        failures = check_assertions("profile", bad, bi_case("stripe"))
        assert any("does not match 'Stripe'" in f for f in failures)
        assert any("matches none of" in f for f in failures)
        assert any("mentions none of" in f for f in failures)
        assert any("offerings, expected at least 3" in f for f in failures)
        assert "contains forbidden text 'Acme Corp'" in failures

    def test_legacy_profile_checks_stay_profile_only(self):
        assert check_assertions("swot", {"strengths": ["Retail"]}, bi_case("stripe")) == []


# --- eleventy: must not invent pricing -------------------------------------


def eleventy_price_pattern() -> re.Pattern[str]:
    [a] = [a for a in parse_assertions(bi_case("eleventy")) if a.forbid_regex]
    return a.forbid_regex[0]


class TestEleventyInventsNoPrices:
    @pytest.mark.parametrize(
        "text",
        ["Build Awesome Pro at $9/month", "costs $49 per year", "€5 per seat", "a £12 licence", "only $0.99",
         "US$ 20 one-off", "$10–$20 per month", "Sponsor tiers from $5,000"],
    )
    def test_invented_prices_are_caught(self, text):
        assert eleventy_price_pattern().search(text), text

    @pytest.mark.parametrize(
        "text",
        [
            "In a $10–12 B global TAM",  # verbatim shape from a stored report
            "SOM: $200–300 M",
            "a $500M market",
            "worth $2.5 billion by 2030",
            "between $1.2-1.8 billion",
            "$10M-$50M SAM",
            "TAM of $3 to $4 bn",
            "Eleventy is free ($0)",
            "downloaded 19,997,961 times by 838 backers",
        ],
    )
    def test_market_sizes_and_free_are_not_prices(self, text):
        assert not eleventy_price_pattern().search(text), text

    def test_a_real_report_that_restates_market_size_does_not_trip_it(self):
        """The stored basecamp report quotes TAM/SAM/SOM in dollars five times.
        Restating an estimate is not inventing a price."""
        text = (KNOWN_BAD / "report_unclosed_array.json.txt").read_text(encoding="utf-8")
        assert re.search(r"\$\d", text), "the sample should contain dollar figures at all"
        assert not eleventy_price_pattern().search(text)

    def test_the_page_itself_quotes_no_price(self):
        """So a faithful answer cannot trip the forbid by quoting the page."""
        scraped = scrape_fixture(bi_case("eleventy"))
        page = " ".join([scraped.title, scraped.description, *scraped.headings, scraped.text_content])
        assert not eleventy_price_pattern().search(page)

    @pytest.mark.parametrize(
        "pricing",
        [
            "Free and open source; funded by sponsors",
            "Open-source, no cost to users",
            "Freemium: the core is free, Build Awesome Pro is paid",
            "Community-funded through a Kickstarter with 838 backers",
            "Donations via Open Collective",
        ],
    )
    def test_honest_pricing_strategies_pass(self, pricing):
        assert failures_for(bi_case("eleventy"), "market", {"pricing_strategy": pricing}) == []

    @pytest.mark.parametrize(
        "pricing",
        ["Tiered subscription pricing with an enterprise plan", "Value-based pricing for agencies", "Premium pricing"],
    )
    def test_an_invented_business_model_fails(self, pricing):
        assert len(failures_for(bi_case("eleventy"), "market", {"pricing_strategy": pricing})) == 1

    def test_only_pricing_strategy_is_read_for_that_check(self):
        value = {"pricing_strategy": "Tiered SaaS", "market_positioning": "free and open source"}
        assert len(failures_for(bi_case("eleventy"), "market", value)) == 1

    def test_the_market_stage_may_quote_a_market_size(self):
        value = {"pricing_strategy": "Free, sponsor-funded", "market_size": "$1-2B (estimate)"}
        assert failures_for(bi_case("eleventy"), "market", value) == []


# --- govuk: must recognise it is not a business ----------------------------


class TestGovukIsNotABusiness:
    @pytest.mark.parametrize(
        "industry",
        ["Government / Public Services", "Public sector digital services", "E-Government",
         "Civic technology", "Central government information portal"],
    )
    def test_a_government_industry_passes(self, industry):
        assert failures_for(bi_case("govuk"), "profile", {"industry": industry}) == []

    @pytest.mark.parametrize("industry", ["SaaS", "Information Technology", "Online information services"])
    def test_a_commercial_or_vague_industry_fails(self, industry):
        """The older industry_terms check passes "Information Technology"."""
        assert len(failures_for(bi_case("govuk"), "profile", {"industry": industry})) == 1

    @pytest.mark.parametrize(
        "text",
        [
            "Sign up for email subscriptions to get GOV.UK updates",
            "How much Universal Credit will I get per month?",
            "Pay your vehicle tax online",
            "Free to use, funded by taxation",
            "Check your State Pension forecast",
        ],
    )
    def test_ordinary_government_copy_does_not_trip_the_forbid(self, text):
        value = {"industry": "Government", "description": text}
        assert failures_for(bi_case("govuk"), "profile", value) == []
        assert failures_for(bi_case("govuk"), "marketing", {"faq": [{"answer": text}]}) == []

    @pytest.mark.parametrize(
        "text",
        ["Offer a premium plan for businesses", "Start a 14-day free trial", "Introduce tiered pricing plans",
         "Charge a monthly subscription", "A freemium model", "The subscription fee covers support"],
    )
    def test_invented_commercial_terms_fail(self, text):
        assert failures_for(bi_case("govuk"), "marketing", {"call_to_action": text}), text

    def test_report_is_not_held_to_the_commercial_forbid(self):
        """It restates a market stage that the prompt pushes into commercial terms."""
        assert failures_for(bi_case("govuk"), "report", {"executive_summary": "a freemium model"}) == []

    def test_the_page_supports_the_answer_the_canary_wants(self):
        scraped = scrape_fixture(bi_case("govuk"))
        page = " ".join([scraped.title, *scraped.headings, scraped.text_content]).lower()
        assert "government" in page
        commercial = [p for a in parse_assertions(bi_case("govuk")) for p in a.forbid_regex]
        assert not any(p.search(page) for p in commercial)


# --- market: must_include / must_exclude are enforced -----------------------


def market_case() -> dict:
    return load_cases(MARKET_SUITE)[0]


def known_bad_resources() -> MarketResources:
    data = json.loads((KNOWN_BAD / "resources_self_hosted_cicd.json").read_text(encoding="utf-8"))
    return MarketResources.model_validate(data)


class TestMarketAssertionsBite:
    def test_the_output_that_started_this_fails_them(self):
        failures = check_assertions("resources", known_bad_resources(), market_case())
        assert sorted(failures) == sorted(
            [
                "[resources.key_players.name] contains forbidden 'CircleCI'",
                "[resources.key_players.name] contains forbidden 'Travis CI'",
                "[resources] mentions none of ['Drone']",
                "[resources] mentions none of ['Woodpecker']",
                "[resources] mentions none of ['Concourse']",
                "[resources] mentions none of ['Gitea', 'Forgejo']",
            ]
        )

    def test_a_correct_answer_passes(self):
        def entry(name: str) -> dict:
            return {"name": name, "url": f"https://{name.lower()}.example", "description": "self-hosted CI"}

        value = MarketResources(
            key_players=[entry(n) for n in ("Drone", "Woodpecker CI", "Concourse", "Jenkins", "Forgejo Actions")],
            # A hosted service as an *indirect* competitor is fair, and so is a
            # description that compares a tool with one.
            competitor_sites=[{**entry("CircleCI"), "description": "Hosted alternative to Woodpecker"}],
        )
        assert check_assertions("resources", value, market_case()) == []

    def test_the_other_market_stages_are_not_held_to_them(self):
        """A five-forces answer may rightly name GitHub Actions as a substitute."""
        value = {"five_forces": {"threat_of_substitutes": "Hosted CI such as GitHub Actions and CircleCI"}}
        assert check_assertions("macro", value, market_case()) == []

    def test_the_not_enforced_note_is_gone(self):
        text = (Path(__file__).resolve().parents[1] / "evals" / "market_cases.yaml").read_text(encoding="utf-8")
        assert "NOT yet enforced" not in text
