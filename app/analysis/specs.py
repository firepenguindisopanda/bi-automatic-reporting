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
    BIReport,
    BusinessProfile,
    CompetitiveAnalysis,
    MarketAnalysis,
    MarketingAnalysis,
    MarketMacroAnalysis,
    MarketResources,
    MarketSynthesis,
    SWOTAnalysis,
)
from app.pipeline.contracts import (
    StageContract,
    grounded_in_source,
    items_have,
    min_items,
    min_length,
    no_duplicate_names,
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
        "Write a comprehensive, executive-level BI report synthesizing all the above."
    )


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
        min_items("offerings", 3),
        min_items("target_audience", 1),
    ),
    context_rules=(grounded_in_source("company_name"),),
)

MARKET_CONTRACT = StageContract(
    rules=(
        required("target_market"),
        required("market_size"),
        required("pricing_strategy"),
        required("market_positioning"),
        min_items("market_trends", 3),
        min_items("customer_segments", 2),
    ),
)

COMPETITIVE_CONTRACT = StageContract(
    rules=(
        min_items("direct_competitors", 3),
        min_items("indirect_competitors", 2),
        items_have("direct_competitors", ("name",)),
        no_duplicate_names("direct_competitors"),
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
        items_have("personas", ("name", "description")),
        min_items("faq", 5),
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

RESOURCES_CONTRACT = StageContract(
    rules=(
        min_items("key_players", 4),
        items_have("key_players", ("name", "url")),
        no_duplicate_names("key_players"),
        min_items("news_sources", 2),
        min_items("industry_websites", 2),
        min_items("competitor_sites", 2),
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
    contract=PROFILE_CONTRACT,
)

MARKET_SPEC = AgentSpec(
    key="market",
    output_model=MarketAnalysis,
    system_prompt=prompts.MARKET_ANALYSIS,
    build_user_prompt=_market_prompt,
    model=settings.agent_model_market_analysis,
    prompt_version=prompts.MARKET_ANALYSIS_V,
    contract=MARKET_CONTRACT,
)

COMPETITIVE_SPEC = AgentSpec(
    key="competitive",
    output_model=CompetitiveAnalysis,
    system_prompt=prompts.COMPETITIVE_ANALYSIS,
    build_user_prompt=_competitive_prompt,
    model=settings.agent_model_competitive_analysis,
    prompt_version=prompts.COMPETITIVE_ANALYSIS_V,
    contract=COMPETITIVE_CONTRACT,
)

SWOT_SPEC = AgentSpec(
    key="swot",
    output_model=SWOTAnalysis,
    system_prompt=prompts.SWOT,
    build_user_prompt=_swot_prompt,
    model=settings.agent_model_swot,
    prompt_version=prompts.SWOT_V,
    contract=SWOT_CONTRACT,
)

MARKETING_SPEC = AgentSpec(
    key="marketing",
    output_model=MarketingAnalysis,
    system_prompt=prompts.MARKETING,
    build_user_prompt=_marketing_prompt,
    model=settings.agent_model_marketing,
    prompt_version=prompts.MARKETING_V,
    contract=MARKETING_CONTRACT,
)

REPORT_SPEC = AgentSpec(
    key="report",
    output_model=BIReport,
    system_prompt=prompts.REPORT_WRITER,
    build_user_prompt=_report_prompt,
    model=settings.agent_model_report_writer,
    prompt_version=prompts.REPORT_WRITER_V,
    contract=REPORT_CONTRACT,
    # The writer restates upstream stages that were already reviewed; a second
    # opinion here mostly re-litigates their scores.
    critic=False,
)

MACRO_SPEC = AgentSpec(
    key="macro",
    output_model=MarketMacroAnalysis,
    system_prompt=prompts.MARKET_MACRO,
    build_user_prompt=_macro_prompt,
    model=settings.agent_model_market_research,
    prompt_version=prompts.MARKET_MACRO_V,
    contract=MACRO_CONTRACT,
)

SIZING_SPEC = AgentSpec(
    key="sizing",
    output_model=MarketSynthesis,
    system_prompt=prompts.MARKET_SIZING,
    build_user_prompt=_sizing_prompt,
    model=settings.agent_model_market_research,
    prompt_version=prompts.MARKET_SIZING_V,
    contract=SIZING_CONTRACT,
)

RESOURCES_SPEC = AgentSpec(
    key="resources",
    output_model=MarketResources,
    system_prompt=prompts.MARKET_RESOURCES,
    build_user_prompt=_resources_prompt,
    model=settings.agent_model_market_research,
    prompt_version=prompts.MARKET_RESOURCES_V,
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
