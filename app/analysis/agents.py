import logging

from app.llm.client import LLMClient
from app.models.bi import (
    BIReport,
    BusinessProfile,
    CompetitiveAnalysis,
    MarketAnalysis,
    MarketingAnalysis,
    ScrapedContent,
    SWOTAnalysis,
)

logger = logging.getLogger(__name__)


class BusinessProfileAgent:
    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    def analyze(self, scraped: ScrapedContent) -> BusinessProfile:
        return self._llm.invoke_structured(
            output_model=BusinessProfile,
            system_prompt=(
                "You are a Business Intelligence Analyst. Extract a structured "
                "business profile from the scraped website content."
            ),
            user_prompt=(
                f"Website URL: {scraped.url}\n"
                f"Title: {scraped.title}\n"
                f"Description: {scraped.description}\n"
                f"Keywords: {', '.join(scraped.meta_keywords)}\n\n"
                f"Headings:\n{chr(10).join(scraped.headings)}\n\n"
                f"Content:\n{scraped.text_content[:15000]}\n\n"
                "Extract the business profile: company name, industry, "
                "offerings, target audience, value proposition, "
                "contact info, and social links."
            ),
        )

    async def analyze_async(self, scraped: ScrapedContent) -> BusinessProfile:
        return await self._llm.ainvoke_structured(
            output_model=BusinessProfile,
            system_prompt=(
                "You are a Business Intelligence Analyst. Extract a structured "
                "business profile from the scraped website content."
            ),
            user_prompt=(
                f"Website URL: {scraped.url}\n"
                f"Title: {scraped.title}\n"
                f"Description: {scraped.description}\n"
                f"Keywords: {', '.join(scraped.meta_keywords)}\n\n"
                f"Headings:\n{chr(10).join(scraped.headings)}\n\n"
                f"Content:\n{scraped.text_content[:15000]}\n\n"
                "Extract the business profile: company name, industry, "
                "offerings, target audience, value proposition, "
                "contact info, and social links."
            ),
        )


class MarketAnalysisAgent:
    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    def analyze(self, profile: BusinessProfile, scraped: ScrapedContent) -> MarketAnalysis:
        return self._llm.invoke_structured(
            output_model=MarketAnalysis,
            system_prompt=(
                "You are a Market Research Analyst. Analyze the market "
                "positioning and target market for this business."
            ),
            user_prompt=(
                f"Company: {profile.company_name}\n"
                f"Industry: {profile.industry}\n"
                f"Description: {profile.description}\n"
                f"Offerings: {', '.join(profile.offerings)}\n"
                f"Target Audience: {', '.join(profile.target_audience)}\n"
                f"Value Proposition: {profile.value_proposition}\n\n"
                f"Website Content:\n{scraped.text_content[:10000]}\n\n"
                "Analyze the target market, market size, trends, customer "
                "segments, pricing strategy, and market positioning."
            ),
        )

    async def analyze_async(self, profile: BusinessProfile, scraped: ScrapedContent) -> MarketAnalysis:
        return await self._llm.ainvoke_structured(
            output_model=MarketAnalysis,
            system_prompt=(
                "You are a Market Research Analyst. Analyze the market "
                "positioning and target market for this business."
            ),
            user_prompt=(
                f"Company: {profile.company_name}\n"
                f"Industry: {profile.industry}\n"
                f"Description: {profile.description}\n"
                f"Offerings: {', '.join(profile.offerings)}\n"
                f"Target Audience: {', '.join(profile.target_audience)}\n"
                f"Value Proposition: {profile.value_proposition}\n\n"
                f"Website Content:\n{scraped.text_content[:10000]}\n\n"
                "Analyze the target market, market size, trends, customer "
                "segments, pricing strategy, and market positioning."
            ),
        )


class CompetitiveAnalysisAgent:
    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    def analyze(
        self, profile: BusinessProfile, market: MarketAnalysis, scraped: ScrapedContent
    ) -> CompetitiveAnalysis:
        return self._llm.invoke_structured(
            output_model=CompetitiveAnalysis,
            system_prompt=(
                "You are a Competitive Intelligence Analyst. Identify "
                "competitors and analyze competitive positioning."
            ),
            user_prompt=(
                f"Company: {profile.company_name}\n"
                f"Industry: {profile.industry}\n"
                f"Offerings: {', '.join(profile.offerings)}\n"
                f"Target Market: {market.target_market}\n"
                f"Market Trends: {', '.join(market.market_trends)}\n\n"
                f"Website Content:\n{scraped.text_content[:8000]}\n\n"
                "Identify direct and indirect competitors, competitive "
                "advantages, market gaps, and provide a threat assessment."
            ),
        )

    async def analyze_async(
        self, profile: BusinessProfile, market: MarketAnalysis, scraped: ScrapedContent
    ) -> CompetitiveAnalysis:
        return await self._llm.ainvoke_structured(
            output_model=CompetitiveAnalysis,
            system_prompt=(
                "You are a Competitive Intelligence Analyst. Identify "
                "competitors and analyze competitive positioning."
            ),
            user_prompt=(
                f"Company: {profile.company_name}\n"
                f"Industry: {profile.industry}\n"
                f"Offerings: {', '.join(profile.offerings)}\n"
                f"Target Market: {market.target_market}\n"
                f"Market Trends: {', '.join(market.market_trends)}\n\n"
                f"Website Content:\n{scraped.text_content[:8000]}\n\n"
                "Identify direct and indirect competitors, competitive "
                "advantages, market gaps, and provide a threat assessment."
            ),
        )


class SWOTAgent:
    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    def analyze(
        self,
        profile: BusinessProfile,
        market: MarketAnalysis,
        competitive: CompetitiveAnalysis,
    ) -> SWOTAnalysis:
        return self._llm.invoke_structured(
            output_model=SWOTAnalysis,
            system_prompt=(
                "You are a Strategic Analyst. Generate a comprehensive "
                "SWOT analysis for this business."
            ),
            user_prompt=(
                f"Company: {profile.company_name}\n"
                f"Offerings: {', '.join(profile.offerings)}\n"
                f"Value Prop: {profile.value_proposition}\n"
                f"Target Market: {market.target_market}\n"
                f"Competitive Advantages: {', '.join(competitive.competitive_advantages)}\n"
                f"Market Gaps: {', '.join(competitive.market_gaps)}\n"
                f"Threat Assessment: {competitive.threat_assessment}\n\n"
                "Generate a SWOT analysis with at least 4 items per category. "
                "Be specific to this business — not generic SWOT items."
            ),
        )

    async def analyze_async(
        self,
        profile: BusinessProfile,
        market: MarketAnalysis,
        competitive: CompetitiveAnalysis,
    ) -> SWOTAnalysis:
        return await self._llm.ainvoke_structured(
            output_model=SWOTAnalysis,
            system_prompt=(
                "You are a Strategic Analyst. Generate a comprehensive "
                "SWOT analysis for this business."
            ),
            user_prompt=(
                f"Company: {profile.company_name}\n"
                f"Offerings: {', '.join(profile.offerings)}\n"
                f"Value Prop: {profile.value_proposition}\n"
                f"Target Market: {market.target_market}\n"
                f"Competitive Advantages: {', '.join(competitive.competitive_advantages)}\n"
                f"Market Gaps: {', '.join(competitive.market_gaps)}\n"
                f"Threat Assessment: {competitive.threat_assessment}\n\n"
                "Generate a SWOT analysis with at least 4 items per category. "
                "Be specific to this business — not generic SWOT items."
            ),
        )


class BIReportWriterAgent:
    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    def write(
        self,
        profile: BusinessProfile,
        market: MarketAnalysis,
        competitive: CompetitiveAnalysis,
        swot: SWOTAnalysis,
    ) -> BIReport:
        return self._llm.invoke_structured(
            output_model=BIReport,
            system_prompt=(
                "You are a Senior Business Analyst writing a professional "
                "business intelligence report. Synthesize all the analysis "
                "into a cohesive, actionable report."
            ),
            user_prompt=(
                f"Business Profile:\n{profile.model_dump_json(indent=2)}\n\n"
                f"Market Analysis:\n{market.model_dump_json(indent=2)}\n\n"
                f"Competitive Analysis:\n{competitive.model_dump_json(indent=2)}\n\n"
                f"SWOT Analysis:\n{swot.model_dump_json(indent=2)}\n\n"
                "Write a comprehensive BI report with: executive summary, "
                "synthesized business profile, market analysis, competitive "
                "analysis, SWOT summary, actionable recommendations (at least 5), "
                "and risk factors (at least 3). Make it professional and insightful."
            ),
        )

    async def write_async(
        self,
        profile: BusinessProfile,
        market: MarketAnalysis,
        competitive: CompetitiveAnalysis,
        swot: SWOTAnalysis,
    ) -> BIReport:
        return await self._llm.ainvoke_structured(
            output_model=BIReport,
            system_prompt=(
                "You are a Senior Business Analyst writing a professional "
                "business intelligence report. Synthesize all the analysis "
                "into a cohesive, actionable report."
            ),
            user_prompt=(
                f"Business Profile:\n{profile.model_dump_json(indent=2)}\n\n"
                f"Market Analysis:\n{market.model_dump_json(indent=2)}\n\n"
                f"Competitive Analysis:\n{competitive.model_dump_json(indent=2)}\n\n"
                f"SWOT Analysis:\n{swot.model_dump_json(indent=2)}\n\n"
                "Write a comprehensive BI report with: executive summary, "
                "synthesized business profile, market analysis, competitive "
                "analysis, SWOT summary, actionable recommendations (at least 5), "
                "and risk factors (at least 3). Make it professional and insightful."
            ),
        )


class MarketingAnalysisAgent:
    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    def analyze(self, profile: BusinessProfile, scraped: ScrapedContent) -> MarketingAnalysis:
        return self._llm.invoke_structured(
            output_model=MarketingAnalysis,
            system_prompt=(
                "You are a Marketing and SEO Analyst. Generate a comprehensive "
                "marketing analysis from the scraped website content."
            ),
            user_prompt=(
                f"Company: {profile.company_name}\n"
                f"Industry: {profile.industry}\n"
                f"Description: {profile.description}\n"
                f"Tagline: {profile.tagline}\n"
                f"Value Proposition: {profile.value_proposition}\n"
                f"Target Audience: {', '.join(profile.target_audience)}\n"
                f"Offerings: {', '.join(profile.offerings)}\n"
                f"Website: {profile.website}\n\n"
                f"Website Content:\n{scraped.text_content[:10000]}\n\n"
                "Generate a marketing analysis covering: target audience "
                "personas (name, description, pain points, goals, channels), "
                "brand personality matrix (archetype, traits, voice), "
                "FAQ items from 'People Also Ask' queries, customer journey "
                "stages (awareness → decision), E-E-A-T signal evaluation "
                "(experience, expertise, authoritativeness, trustworthiness), "
                "GEO (Generative Engine Optimization) tactics, "
                "and a clear call to action. "
                "Be specific to this business — not generic."
            ),
        )

    async def analyze_async(self, profile: BusinessProfile, scraped: ScrapedContent) -> MarketingAnalysis:
        return await self._llm.ainvoke_structured(
            output_model=MarketingAnalysis,
            system_prompt=(
                "You are a Marketing and SEO Analyst. Generate a comprehensive "
                "marketing analysis from the scraped website content."
            ),
            user_prompt=(
                f"Company: {profile.company_name}\n"
                f"Industry: {profile.industry}\n"
                f"Description: {profile.description}\n"
                f"Tagline: {profile.tagline}\n"
                f"Value Proposition: {profile.value_proposition}\n"
                f"Target Audience: {', '.join(profile.target_audience)}\n"
                f"Offerings: {', '.join(profile.offerings)}\n"
                f"Website: {profile.website}\n\n"
                f"Website Content:\n{scraped.text_content[:10000]}\n\n"
                "Generate a marketing analysis covering: target audience "
                "personas (name, description, pain points, goals, channels), "
                "brand personality matrix (archetype, traits, voice), "
                "FAQ items from 'People Also Ask' queries, customer journey "
                "stages (awareness → decision), E-E-A-T signal evaluation "
                "(experience, expertise, authoritativeness, trustworthiness), "
                "GEO (Generative Engine Optimization) tactics, "
                "and a clear call to action. "
                "Be specific to this business — not generic."
            ),
        )
