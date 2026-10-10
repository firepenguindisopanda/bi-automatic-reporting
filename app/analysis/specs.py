"""Agent definitions.

Each stage that calls a model is one AgentSpec: which model, which prompt, what
schema to produce, and what a complete answer looks like. The runner does
everything else. Adding a stage means adding a spec here and an entry in
``app.pipeline.stages`` - no new class, no duplicated prompt.
"""

from app.analysis import prompts
from app.analysis.runner import AgentSpec, StageContext
from app.config import settings
from app.models.bi import (
    BusinessProfile,
    CompetitiveAnalysis,
    MarketAnalysis,
    MarketingAnalysis,
    MarketMacroAnalysis,
    MarketResources,
    MarketSynthesis,
    ReportSynthesis,
    SWOTAnalysis,
)
from app.pipeline.contracts import (
    RANGE_PATTERN,
    StageContract,
    field_differs_from,
    grounded_in_source,
    items_have,
    items_have_any,
    items_min_list,
    matches,
    max_items,
    min_items,
    min_length,
    min_words,
    no_cross_list_duplicates,
    no_duplicate_names,
    no_near_identical_items,
    required,
)

# --- User prompt builders --------------------------------------------------
# Kept byte-identical to the original agent implementations: output quality is
# tuned to this exact phrasing and truncation.


def _profile_prompt(ctx: StageContext) -> str:
    s = ctx.require_scraped()
    return (
        f"WEBSITE URL: {s.url}\n"
        f"PAGE TITLE: {s.title}\n"
        f"META DESCRIPTION: {s.description}\n"
        f"META KEYWORDS: {', '.join(s.meta_keywords)}\n\n"
        f"PAGE HEADINGS:\n{chr(10).join(s.headings)}\n\n"
        f"PAGE CONTENT:\n{s.text_content[:15000]}\n\n"
        "Extract and return the structured business profile from this content."
    )


def _market_prompt(ctx: StageContext) -> str:
    p, s = ctx.require_profile(), ctx.require_scraped()
    return (
        f"COMPANY: {p.company_name}\n"
        f"INDUSTRY: {p.industry}\n"
        f"DESCRIPTION: {p.description}\n"
        f"OFFERINGS: {', '.join(p.offerings)}\n"
        f"TARGET AUDIENCE: {', '.join(p.target_audience)}\n"
        f"VALUE PROPOSITION: {p.value_proposition}\n\n"
        f"WEBSITE CONTENT:\n{s.text_content[:10000]}\n\n"
        "Provide a comprehensive market analysis for this business."
    )


def _competitive_prompt(ctx: StageContext) -> str:
    p, m, s = ctx.require_profile(), ctx.require_market(), ctx.require_scraped()
    return (
        f"COMPANY: {p.company_name}\n"
        f"INDUSTRY: {p.industry}\n"
        f"OFFERINGS: {', '.join(p.offerings)}\n"
        f"TARGET MARKET: {m.target_market}\n"
        f"MARKET TRENDS: {', '.join(m.market_trends)}\n\n"
        f"WEBSITE CONTENT:\n{s.text_content[:8000]}\n\n"
        "Provide a comprehensive competitive analysis for this business."
    )


def _swot_prompt(ctx: StageContext) -> str:
    p, m, c = ctx.require_profile(), ctx.require_market(), ctx.require_competitive()
    return (
        f"COMPANY: {p.company_name}\n"
        f"INDUSTRY: {p.industry}\n"
        f"OFFERINGS: {', '.join(p.offerings)}\n"
        f"VALUE PROPOSITION: {p.value_proposition}\n"
        f"TARGET MARKET: {m.target_market}\n"
        f"MARKET TRENDS: {', '.join(m.market_trends)}\n"
        f"COMPETITIVE ADVANTAGES: {', '.join(c.competitive_advantages)}\n"
        f"MARKET GAPS: {', '.join(c.market_gaps)}\n"
        f"THREAT ASSESSMENT: {c.threat_assessment}\n\n"
        "Generate a comprehensive, specific SWOT analysis for this business."
    )


def _marketing_prompt(ctx: StageContext) -> str:
    p, s = ctx.require_profile(), ctx.require_scraped()
    return (
        f"COMPANY: {p.company_name}\n"
        f"INDUSTRY: {p.industry}\n"
        f"DESCRIPTION: {p.description}\n"
        f"TAGLINE: {p.tagline}\n"
        f"VALUE PROPOSITION: {p.value_proposition}\n"
        f"TARGET AUDIENCE: {', '.join(p.target_audience)}\n"
        f"OFFERINGS: {', '.join(p.offerings)}\n"
        f"WEBSITE: {p.website}\n\n"
        f"WEBSITE CONTENT:\n{s.text_content[:10000]}\n\n"
        "Generate a comprehensive marketing analysis and strategy for this business."
    )


def _report_prompt(ctx: StageContext) -> str:
    p, m = ctx.require_profile(), ctx.require_market()
    c, w = ctx.require_competitive(), ctx.require_swot()
    return (
        f"BUSINESS PROFILE:\n{p.model_dump_json(indent=2)}\n\n"
        f"MARKET ANALYSIS:\n{m.model_dump_json(indent=2)}\n\n"
        f"COMPETITIVE ANALYSIS:\n{c.model_dump_json(indent=2)}\n\n"
        f"SWOT ANALYSIS:\n{w.model_dump_json(indent=2)}\n\n"
        "Write the executive summary, recommendations and risk factors for this "
        "business, synthesizing the analyses above."
    )


MAX_CRITIC_SOURCE_CHARS = 12000


def _report_critic_source(ctx: StageContext) -> str:
    """The analyses the report writer was given, compact so more of it fits."""
    p, m = ctx.require_profile(), ctx.require_market()
    c, w = ctx.require_competitive(), ctx.require_swot()
    text = (
        f"BUSINESS PROFILE: {p.model_dump_json()}\n\n"
        f"MARKET ANALYSIS: {m.model_dump_json()}\n\n"
        f"COMPETITIVE ANALYSIS: {c.model_dump_json()}\n\n"
        f"SWOT ANALYSIS: {w.model_dump_json()}"
    )
    return text[:MAX_CRITIC_SOURCE_CHARS]


def _macro_prompt(ctx: StageContext) -> str:
    return (
        f"MARKET TO RESEARCH: {ctx.market_query}\n\n"
        "Apply PESTEL and Porter's Five Forces to this market. "
        "Return the complete structured analysis."
    )


def _sizing_prompt(ctx: StageContext) -> str:
    macro = ctx.require_macro()
    return (
        f"MARKET TO RESEARCH: {ctx.market_query}\n\n"
        f"MACRO AND INDUSTRY STRUCTURE ANALYSIS ALREADY COMPLETED:\n"
        f"{macro.model_dump_json(indent=2)}\n\n"
        "Size this market (TAM/SAM/SOM and growth), synthesize a SWOT for the "
        "market as a whole, and write the executive summary, key insights, "
        "recommendations and risks. Build on the macro analysis above rather "
        "than repeating it."
    )


def _resources_prompt(ctx: StageContext) -> str:
    sizing = ctx.require_sizing()
    return (
        f"MARKET TO RESEARCH: {ctx.market_query}\n\n"
        f"ANALYSIS ALREADY COMPLETED:\n{sizing.model_dump_json(indent=2)}\n\n"
        "Compile the six categories of real, verifiable resources for this market."
    )


# --- Contracts -------------------------------------------------------------
# What a complete answer looks like. Scored, never enforced.

PROFILE_CONTRACT = StageContract(
    rules=(
        required("company_name"),
        required("industry"),
        required("value_proposition"),
        # The prompt asks for "the sector and the niche, not a one-word
        # category". "Technology" is the classic miss.
        min_words("industry", 2),
        min_items("offerings", 3),
        min_items("target_audience", 1),
    ),
    context_rules=(grounded_in_source("company_name"),),
)

MARKET_CONTRACT = StageContract(
    rules=(
        required("target_market"),
        required("market_size"),
        matches("market_size", RANGE_PATTERN, "is not a range; the prompt asks for one"),
        required("pricing_strategy"),
        required("market_positioning"),
        min_items("market_trends", 3),
        min_items("customer_segments", 2),
    ),
)

COMPETITIVE_CONTRACT = StageContract(
    rules=(
        # The prompt asks for 3-5 direct and 2-3 indirect competitors. Past the
        # upper bound the extra entries are usually invented to fill space.
        min_items("direct_competitors", 3),
        max_items("direct_competitors", 5),
        min_items("indirect_competitors", 2),
        max_items("indirect_competitors", 3),
        items_have("direct_competitors", ("name",)),
        items_have("indirect_competitors", ("name",)),
        items_have_any("direct_competitors", ("offering", "description"), "what it offers"),
        items_have_any("indirect_competitors", ("offering", "description"), "what it offers"),
        no_duplicate_names("direct_competitors"),
        no_duplicate_names("indirect_competitors"),
        # A company cannot coherently be both a direct and an indirect
        # competitor. Unlike the resources rules this is a logical
        # contradiction rather than a style judgement, so it needs no threshold.
        no_cross_list_duplicates(("direct_competitors", "indirect_competitors")),
        min_items("competitive_advantages", 2),
        min_items("market_gaps", 2),
        min_length("threat_assessment", 120),
    ),
)

# The SWOT prompt asks for a minimum of 5 per quadrant; nothing checked it before.
SWOT_CONTRACT = StageContract(
    rules=(
        min_items("strengths", 5),
        min_items("weaknesses", 5),
        min_items("opportunities", 5),
        min_items("threats", 5),
    ),
)

MARKETING_CONTRACT = StageContract(
    rules=(
        min_items("personas", 2),
        max_items("personas", 4),
        items_have("personas", ("name", "description")),
        items_min_list("personas", "pain_points", 2),
        min_items("faq", 5),
        max_items("faq", 8),
        min_items("customer_journey", 4),
        min_items("geo_tactics", 2),
        required("brand_personality"),
        required("eeat"),
        required("call_to_action"),
    ),
)

REPORT_CONTRACT = StageContract(
    rules=(
        min_length("executive_summary", 400),
        min_items("recommendations", 5),
        min_items("risk_factors", 3),
    ),
)

MACRO_CONTRACT = StageContract(
    rules=(
        min_items("pestel.political", 1),
        min_items("pestel.economic", 1),
        min_items("pestel.social", 1),
        min_items("pestel.technological", 1),
        min_items("pestel.environmental", 1),
        min_items("pestel.legal", 1),
        required("five_forces.threat_of_new_entrants"),
        required("five_forces.supplier_power"),
        required("five_forces.buyer_power"),
        required("five_forces.threat_of_substitutes"),
        required("five_forces.competitive_rivalry"),
    ),
)

SIZING_CONTRACT = StageContract(
    rules=(
        min_length("executive_summary", 100),
        min_length("summary", 300),
        required("market_size"),
        min_items("key_insights", 3),
        min_items("recommendations", 3),
        min_items("risks_and_assumptions", 2),
        min_items("swot.strengths", 3),
        min_items("swot.threats", 3),
    ),
)

# Everything below the length checks was added by M4. The output that shipped
# CircleCI and Travis CI as "self-hosted" passed the original six rules and
# scored 100.0 while carrying 18 distinct names across 21 entries, one sentence
# reused verbatim, and relevance fields that restated their own descriptions.
#
# Scoped to key_players and competitor_sites because that is where the evidence
# is. The same rules also fire on research_papers (relevance echo, 0.69) and
# relevant_communities (near-identical, 0.87) in that stored output, but both
# sit close enough to the threshold that enforcing them would be building the
# score on borderline calls. Recorded, deliberately not acted on yet.
RESOURCES_CONTRACT = StageContract(
    rules=(
        min_items("key_players", 4),
        items_have("key_players", ("name", "url")),
        no_duplicate_names("key_players"),
        min_items("news_sources", 2),
        min_items("industry_websites", 2),
        min_items("competitor_sites", 2),
        no_cross_list_duplicates(("key_players", "competitor_sites")),
        field_differs_from("key_players", "description", "relevance"),
        no_near_identical_items("key_players"),
        field_differs_from("competitor_sites", "description", "relevance"),
        no_near_identical_items("competitor_sites"),
    ),
)


# --- Specs -----------------------------------------------------------------

PROFILE_SPEC = AgentSpec(
    key="profile",
    output_model=BusinessProfile,
    system_prompt=prompts.BUSINESS_PROFILE,
    build_user_prompt=_profile_prompt,
    model=settings.agent_model_business_profile,
    prompt_version=prompts.BUSINESS_PROFILE_V,
    max_attempts=settings.agent_max_attempts,
    fallback_model=settings.agent_fallback_model or None,
    contract=PROFILE_CONTRACT,
)

MARKET_SPEC = AgentSpec(
    key="market",
    output_model=MarketAnalysis,
    system_prompt=prompts.MARKET_ANALYSIS,
    build_user_prompt=_market_prompt,
    model=settings.agent_model_market_analysis,
    prompt_version=prompts.MARKET_ANALYSIS_V,
    max_attempts=settings.agent_max_attempts,
    fallback_model=settings.agent_fallback_model or None,
    contract=MARKET_CONTRACT,
)

COMPETITIVE_SPEC = AgentSpec(
    key="competitive",
    output_model=CompetitiveAnalysis,
    system_prompt=prompts.COMPETITIVE_ANALYSIS,
    build_user_prompt=_competitive_prompt,
    model=settings.agent_model_competitive_analysis,
    prompt_version=prompts.COMPETITIVE_ANALYSIS_V,
    max_attempts=settings.agent_max_attempts,
    fallback_model=settings.agent_fallback_model or None,
    contract=COMPETITIVE_CONTRACT,
)

SWOT_SPEC = AgentSpec(
    key="swot",
    output_model=SWOTAnalysis,
    system_prompt=prompts.SWOT,
    build_user_prompt=_swot_prompt,
    model=settings.agent_model_swot,
    prompt_version=prompts.SWOT_V,
    max_attempts=settings.agent_max_attempts,
    fallback_model=settings.agent_fallback_model or None,
    contract=SWOT_CONTRACT,
)

MARKETING_SPEC = AgentSpec(
    key="marketing",
    output_model=MarketingAnalysis,
    system_prompt=prompts.MARKETING,
    build_user_prompt=_marketing_prompt,
    model=settings.agent_model_marketing,
    prompt_version=prompts.MARKETING_V,
    max_attempts=settings.agent_max_attempts,
    fallback_model=settings.agent_fallback_model or None,
    contract=MARKETING_CONTRACT,
)

REPORT_SPEC = AgentSpec(
    key="report",
    output_model=ReportSynthesis,
    system_prompt=prompts.REPORT_WRITER,
    build_user_prompt=_report_prompt,
    model=settings.agent_model_report_writer,
    prompt_version=prompts.REPORT_WRITER_V,
    max_attempts=settings.agent_max_attempts,
    fallback_model=settings.agent_fallback_model or None,
    contract=REPORT_CONTRACT,
    # Reviewed like every other stage now that it writes only its own
    # synthesis rather than restating stages that were already reviewed - and
    # judged against those analyses, which are what it was given.
    critic_source=_report_critic_source,
)

MACRO_SPEC = AgentSpec(
    key="macro",
    output_model=MarketMacroAnalysis,
    system_prompt=prompts.MARKET_MACRO,
    build_user_prompt=_macro_prompt,
    model=settings.agent_model_market_research,
    prompt_version=prompts.MARKET_MACRO_V,
    max_attempts=settings.agent_max_attempts,
    fallback_model=settings.agent_fallback_model or None,
    contract=MACRO_CONTRACT,
)

SIZING_SPEC = AgentSpec(
    key="sizing",
    output_model=MarketSynthesis,
    system_prompt=prompts.MARKET_SIZING,
    build_user_prompt=_sizing_prompt,
    model=settings.agent_model_market_research,
    prompt_version=prompts.MARKET_SIZING_V,
    max_attempts=settings.agent_max_attempts,
    fallback_model=settings.agent_fallback_model or None,
    contract=SIZING_CONTRACT,
)

RESOURCES_SPEC = AgentSpec(
    key="resources",
    output_model=MarketResources,
    system_prompt=prompts.MARKET_RESOURCES,
    build_user_prompt=_resources_prompt,
    model=settings.agent_model_market_research,
    prompt_version=prompts.MARKET_RESOURCES_V,
    max_attempts=settings.agent_max_attempts,
    fallback_model=settings.agent_fallback_model or None,
    contract=RESOURCES_CONTRACT,
)

BI_SPECS = (
    PROFILE_SPEC,
    MARKET_SPEC,
    COMPETITIVE_SPEC,
    SWOT_SPEC,
    MARKETING_SPEC,
    REPORT_SPEC,
)

MARKET_RESEARCH_SPECS = (MACRO_SPEC, SIZING_SPEC, RESOURCES_SPEC)
