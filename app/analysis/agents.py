import logging

from app.config import settings
from app.llm.client import LLMClient
from app.models.bi import (
    BIReport,
    BusinessProfile,
    CompetitiveAnalysis,
    MarketAnalysis,
    MarketingAnalysis,
    MarketResearchResult,
    ScrapedContent,
    SWOTAnalysis,
)

logger = logging.getLogger(__name__)


class BusinessProfileAgent:
    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    def _call(self, scraped: ScrapedContent) -> BusinessProfile:
        return self._llm.with_model(settings.agent_model_business_profile).invoke_structured(
            output_model=BusinessProfile,
            system_prompt=(
                "You are a senior Business Intelligence Analyst at a top consulting firm. "
                "Your role is to extract precise, structured business profiles from website content. "
                "You have 15+ years of experience analyzing companies across all industries.\n\n"
                "REQUIREMENTS:\n"
                "- Extract ONLY information that is explicitly stated or strongly implied in the content\n"
                "- Never fabricate details - use empty strings/lists for unverifiable fields\n"
                "- Company name must be the exact legal or trading name found on the site\n"
                "- Industry must be specific (e.g., 'SaaS E-commerce Analytics' not just 'Technology')\n"
                "- Offerings should be specific products/services, not generic categories\n"
                "- Target audience should be inferred from tone, pricing, and content focus\n"
                "- Value proposition should be the single clearest benefit stated\n"
                "- Contact info only if explicitly displayed (email, phone, address)\n"
                "- Social links only for major platforms (LinkedIn, Twitter, Facebook, Instagram, YouTube)\n\n"
                "OUTPUT QUALITY:\n"
                "- Be concise but comprehensive\n"
                "- Use the company's own terminology where possible\n"
                "- Prioritize factual content over marketing language"
            ),
            user_prompt=(
                f"WEBSITE URL: {scraped.url}\n"
                f"PAGE TITLE: {scraped.title}\n"
                f"META DESCRIPTION: {scraped.description}\n"
                f"META KEYWORDS: {', '.join(scraped.meta_keywords)}\n\n"
                f"PAGE HEADINGS:\n{chr(10).join(scraped.headings)}\n\n"
                f"PAGE CONTENT:\n{scraped.text_content[:15000]}\n\n"
                "Extract and return the structured business profile from this content."
            ),
        )

    async def _call_async(self, scraped: ScrapedContent) -> BusinessProfile:
        return await self._llm.with_model(settings.agent_model_business_profile).ainvoke_structured(
            output_model=BusinessProfile,
            system_prompt=(
                "You are a senior Business Intelligence Analyst at a top consulting firm. "
                "Your role is to extract precise, structured business profiles from website content. "
                "You have 15+ years of experience analyzing companies across all industries.\n\n"
                "REQUIREMENTS:\n"
                "- Extract ONLY information that is explicitly stated or strongly implied in the content\n"
                "- Never fabricate details - use empty strings/lists for unverifiable fields\n"
                "- Company name must be the exact legal or trading name found on the site\n"
                "- Industry must be specific (e.g., 'SaaS E-commerce Analytics' not just 'Technology')\n"
                "- Offerings should be specific products/services, not generic categories\n"
                "- Target audience should be inferred from tone, pricing, and content focus\n"
                "- Value proposition should be the single clearest benefit stated\n"
                "- Contact info only if explicitly displayed (email, phone, address)\n"
                "- Social links only for major platforms (LinkedIn, Twitter, Facebook, Instagram, YouTube)\n\n"
                "OUTPUT QUALITY:\n"
                "- Be concise but comprehensive\n"
                "- Use the company's own terminology where possible\n"
                "- Prioritize factual content over marketing language"
            ),
            user_prompt=(
                f"WEBSITE URL: {scraped.url}\n"
                f"PAGE TITLE: {scraped.title}\n"
                f"META DESCRIPTION: {scraped.description}\n"
                f"META KEYWORDS: {', '.join(scraped.meta_keywords)}\n\n"
                f"PAGE HEADINGS:\n{chr(10).join(scraped.headings)}\n\n"
                f"PAGE CONTENT:\n{scraped.text_content[:15000]}\n\n"
                "Extract and return the structured business profile from this content."
            ),
        )

    def analyze(self, scraped: ScrapedContent) -> BusinessProfile:
        return self._call(scraped)

    async def analyze_async(self, scraped: ScrapedContent) -> BusinessProfile:
        return await self._call_async(scraped)


class MarketAnalysisAgent:
    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    def _call(self, profile: BusinessProfile, scraped: ScrapedContent) -> MarketAnalysis:
        return self._llm.with_model(settings.agent_model_market_analysis).invoke_structured(
            output_model=MarketAnalysis,
            system_prompt=(
                "You are a senior Market Research Analyst at a global strategy firm. "
                "You specialize in analyzing market positioning, sizing, and trends "
                "across B2B and B2C markets. Your analyses are used by Fortune 500 executives "
                "to make strategic decisions.\n\n"
                "ANALYSIS FRAMEWORK:\n"
                "1. Define the precise target market(s) the business operates in\n"
                "2. Estimate market size with realistic ranges (TAM, SAM, SOM where inferable)\n"
                "3. Identify 3-5 current market trends affecting this industry\n"
                "4. Segment customers by meaningful differentiators (not just demographics)\n"
                "5. Analyze pricing strategy relative to market position\n"
                "6. Assess market positioning - how the brand is perceived vs competitors\n\n"
                "REQUIREMENTS:\n"
                "- Base all analysis on the provided content and your business expertise\n"
                "- Market size should be a qualitative range ('$500M-$2B'), not a specific number\n"
                "- Trends must be current and specific to this industry/niche\n"
                "- Customer segments should be actionable (e.g., 'SME retailers with 10-50 employees')\n"
                "- Pricing strategy must reference actual approaches (freemium, tiered, value-based, etc.)\n"
                "- Positioning should reference specific differentiators, not generic statements"
            ),
            user_prompt=(
                f"COMPANY: {profile.company_name}\n"
                f"INDUSTRY: {profile.industry}\n"
                f"DESCRIPTION: {profile.description}\n"
                f"OFFERINGS: {', '.join(profile.offerings)}\n"
                f"TARGET AUDIENCE: {', '.join(profile.target_audience)}\n"
                f"VALUE PROPOSITION: {profile.value_proposition}\n\n"
                f"WEBSITE CONTENT:\n{scraped.text_content[:10000]}\n\n"
                "Provide a comprehensive market analysis for this business."
            ),
        )

    async def _call_async(self, profile: BusinessProfile, scraped: ScrapedContent) -> MarketAnalysis:
        return await self._llm.with_model(settings.agent_model_market_analysis).ainvoke_structured(
            output_model=MarketAnalysis,
            system_prompt=(
                "You are a senior Market Research Analyst at a global strategy firm. "
                "You specialize in analyzing market positioning, sizing, and trends "
                "across B2B and B2C markets. Your analyses are used by Fortune 500 executives "
                "to make strategic decisions.\n\n"
                "ANALYSIS FRAMEWORK:\n"
                "1. Define the precise target market(s) the business operates in\n"
                "2. Estimate market size with realistic ranges (TAM, SAM, SOM where inferable)\n"
                "3. Identify 3-5 current market trends affecting this industry\n"
                "4. Segment customers by meaningful differentiators (not just demographics)\n"
                "5. Analyze pricing strategy relative to market position\n"
                "6. Assess market positioning - how the brand is perceived vs competitors\n\n"
                "REQUIREMENTS:\n"
                "- Base all analysis on the provided content and your business expertise\n"
                "- Market size should be a qualitative range ('$500M-$2B'), not a specific number\n"
                "- Trends must be current and specific to this industry/niche\n"
                "- Customer segments should be actionable (e.g., 'SME retailers with 10-50 employees')\n"
                "- Pricing strategy must reference actual approaches (freemium, tiered, value-based, etc.)\n"
                "- Positioning should reference specific differentiators, not generic statements"
            ),
            user_prompt=(
                f"COMPANY: {profile.company_name}\n"
                f"INDUSTRY: {profile.industry}\n"
                f"DESCRIPTION: {profile.description}\n"
                f"OFFERINGS: {', '.join(profile.offerings)}\n"
                f"TARGET AUDIENCE: {', '.join(profile.target_audience)}\n"
                f"VALUE PROPOSITION: {profile.value_proposition}\n\n"
                f"WEBSITE CONTENT:\n{scraped.text_content[:10000]}\n\n"
                "Provide a comprehensive market analysis for this business."
            ),
        )

    def analyze(self, profile: BusinessProfile, scraped: ScrapedContent) -> MarketAnalysis:
        return self._call(profile, scraped)

    async def analyze_async(self, profile: BusinessProfile, scraped: ScrapedContent) -> MarketAnalysis:
        return await self._call_async(profile, scraped)


class CompetitiveAnalysisAgent:
    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    def _call(self, profile: BusinessProfile, market: MarketAnalysis, scraped: ScrapedContent) -> CompetitiveAnalysis:
        return self._llm.with_model(settings.agent_model_competitive_analysis).invoke_structured(
            output_model=CompetitiveAnalysis,
            system_prompt=(
                "You are a senior Competitive Intelligence Analyst at a top strategy consultancy. "
                "You specialize in identifying competitive landscapes, assessing threats, "
                "and uncovering market opportunities. Your insights drive M&A decisions and "
                "go-to-market strategies for leading companies.\n\n"
                "ANALYSIS FRAMEWORK:\n"
                "1. Identify 3-5 DIRECT competitors (same product/service, same customer segment)\n"
                "2. Identify 2-3 INDIRECT competitors (different solution, same customer need)\n"
                "3. Analyze each competitor: name, offering, strengths, weaknesses, market share (if known)\n"
                "4. Identify the business's sustainable competitive advantages\n"
                "5. Spot gaps in the market that competitors are not addressing\n"
                "6. Provide a realistic threat assessment considering market trends\n\n"
                "REQUIREMENTS:\n"
                "- Competitor names must be real, verifiable companies\n"
                "- Strengths/weaknesses should be specific (not 'good product'/'bad support')\n"
                "- Competitive advantages must be genuinely defensible (tech, brand, network effects, etc.)\n"
                "- Market gaps should be actionable opportunities\n"
                "- Threat assessment should consider: new entrants, substitutes, supplier power, buyer power, rivalry"
            ),
            user_prompt=(
                f"COMPANY: {profile.company_name}\n"
                f"INDUSTRY: {profile.industry}\n"
                f"OFFERINGS: {', '.join(profile.offerings)}\n"
                f"TARGET MARKET: {market.target_market}\n"
                f"MARKET TRENDS: {', '.join(market.market_trends)}\n\n"
                f"WEBSITE CONTENT:\n{scraped.text_content[:8000]}\n\n"
                "Provide a comprehensive competitive analysis for this business."
            ),
        )

    async def _call_async(
        self, profile: BusinessProfile, market: MarketAnalysis, scraped: ScrapedContent
    ) -> CompetitiveAnalysis:
        return await self._llm.with_model(settings.agent_model_competitive_analysis).ainvoke_structured(
            output_model=CompetitiveAnalysis,
            system_prompt=(
                "You are a senior Competitive Intelligence Analyst at a top strategy consultancy. "
                "You specialize in identifying competitive landscapes, assessing threats, "
                "and uncovering market opportunities. Your insights drive M&A decisions and "
                "go-to-market strategies for leading companies.\n\n"
                "ANALYSIS FRAMEWORK:\n"
                "1. Identify 3-5 DIRECT competitors (same product/service, same customer segment)\n"
                "2. Identify 2-3 INDIRECT competitors (different solution, same customer need)\n"
                "3. Analyze each competitor: name, offering, strengths, weaknesses, market share (if known)\n"
                "4. Identify the business's sustainable competitive advantages\n"
                "5. Spot gaps in the market that competitors are not addressing\n"
                "6. Provide a realistic threat assessment considering market trends\n\n"
                "REQUIREMENTS:\n"
                "- Competitor names must be real, verifiable companies\n"
                "- Strengths/weaknesses should be specific (not 'good product'/'bad support')\n"
                "- Competitive advantages must be genuinely defensible (tech, brand, network effects, etc.)\n"
                "- Market gaps should be actionable opportunities\n"
                "- Threat assessment should consider: new entrants, substitutes, supplier power, buyer power, rivalry"
            ),
            user_prompt=(
                f"COMPANY: {profile.company_name}\n"
                f"INDUSTRY: {profile.industry}\n"
                f"OFFERINGS: {', '.join(profile.offerings)}\n"
                f"TARGET MARKET: {market.target_market}\n"
                f"MARKET TRENDS: {', '.join(market.market_trends)}\n\n"
                f"WEBSITE CONTENT:\n{scraped.text_content[:8000]}\n\n"
                "Provide a comprehensive competitive analysis for this business."
            ),
        )

    def analyze(self, profile: BusinessProfile, market: MarketAnalysis, scraped: ScrapedContent) -> CompetitiveAnalysis:
        return self._call(profile, market, scraped)

    async def analyze_async(
        self, profile: BusinessProfile, market: MarketAnalysis, scraped: ScrapedContent
    ) -> CompetitiveAnalysis:
        return await self._call_async(profile, market, scraped)


class SWOTAgent:
    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    def _call(self, profile: BusinessProfile, market: MarketAnalysis, competitive: CompetitiveAnalysis) -> SWOTAnalysis:
        return self._llm.with_model(settings.agent_model_swot).invoke_structured(
            output_model=SWOTAnalysis,
            system_prompt=(
                "You are a senior Strategy Analyst with expertise in corporate strategy, "
                "having led SWOT analyses for McKinsey, BCG, and Bain. You synthesize complex "
                "business data into actionable strategic insights.\n\n"
                "ANALYSIS FRAMEWORK:\n"
                "Strengths (INTERNAL, POSITIVE):\n"
                "- What unique resources/capabilities does the business have?\n"
                "- What do they do better than competitors?\n"
                "- What proprietary assets (IP, brand, talent) do they own?\n\n"
                "Weaknesses (INTERNAL, NEGATIVE):\n"
                "- Where are they vulnerable or lacking?\n"
                "- What resources/capabilities are they missing?\n"
                "- Where do competitors have an advantage?\n\n"
                "Opportunities (EXTERNAL, POSITIVE):\n"
                "- What market trends can they capitalize on?\n"
                "- What unmet customer needs exist?\n"
                "- What technological/regulatory changes benefit them?\n\n"
                "Threats (EXTERNAL, NEGATIVE):\n"
                "- What competitive moves could harm them?\n"
                "- What market/economic shifts pose risks?\n"
                "- What regulatory/technological changes threaten their model?\n\n"
                "REQUIREMENTS:\n"
                "- Minimum 5 items per category\n"
                "- Each item must be specific to THIS business (not generic like 'strong team')\n"
                "- Items should be actionable - each weakness should imply a fix, each opportunity a strategy\n"
                "- Use a consistent format: concise phrase followed by brief explanation\n"
                "- Avoid vague statements - tie each point to specific business context"
            ),
            user_prompt=(
                f"COMPANY: {profile.company_name}\n"
                f"INDUSTRY: {profile.industry}\n"
                f"OFFERINGS: {', '.join(profile.offerings)}\n"
                f"VALUE PROPOSITION: {profile.value_proposition}\n"
                f"TARGET MARKET: {market.target_market}\n"
                f"MARKET TRENDS: {', '.join(market.market_trends)}\n"
                f"COMPETITIVE ADVANTAGES: {', '.join(competitive.competitive_advantages)}\n"
                f"MARKET GAPS: {', '.join(competitive.market_gaps)}\n"
                f"THREAT ASSESSMENT: {competitive.threat_assessment}\n\n"
                "Generate a comprehensive, specific SWOT analysis for this business."
            ),
        )

    async def _call_async(
        self, profile: BusinessProfile, market: MarketAnalysis, competitive: CompetitiveAnalysis
    ) -> SWOTAnalysis:
        return await self._llm.with_model(settings.agent_model_swot).ainvoke_structured(
            output_model=SWOTAnalysis,
            system_prompt=(
                "You are a senior Strategy Analyst with expertise in corporate strategy, "
                "having led SWOT analyses for McKinsey, BCG, and Bain. You synthesize complex "
                "business data into actionable strategic insights.\n\n"
                "ANALYSIS FRAMEWORK:\n"
                "Strengths (INTERNAL, POSITIVE):\n"
                "- What unique resources/capabilities does the business have?\n"
                "- What do they do better than competitors?\n"
                "- What proprietary assets (IP, brand, talent) do they own?\n\n"
                "Weaknesses (INTERNAL, NEGATIVE):\n"
                "- Where are they vulnerable or lacking?\n"
                "- What resources/capabilities are they missing?\n"
                "- Where do competitors have an advantage?\n\n"
                "Opportunities (EXTERNAL, POSITIVE):\n"
                "- What market trends can they capitalize on?\n"
                "- What unmet customer needs exist?\n"
                "- What technological/regulatory changes benefit them?\n\n"
                "Threats (EXTERNAL, NEGATIVE):\n"
                "- What competitive moves could harm them?\n"
                "- What market/economic shifts pose risks?\n"
                "- What regulatory/technological changes threaten their model?\n\n"
                "REQUIREMENTS:\n"
                "- Minimum 5 items per category\n"
                "- Each item must be specific to THIS business (not generic like 'strong team')\n"
                "- Items should be actionable - each weakness should imply a fix, each opportunity a strategy\n"
                "- Use a consistent format: concise phrase followed by brief explanation\n"
                "- Avoid vague statements - tie each point to specific business context"
            ),
            user_prompt=(
                f"COMPANY: {profile.company_name}\n"
                f"INDUSTRY: {profile.industry}\n"
                f"OFFERINGS: {', '.join(profile.offerings)}\n"
                f"VALUE PROPOSITION: {profile.value_proposition}\n"
                f"TARGET MARKET: {market.target_market}\n"
                f"MARKET TRENDS: {', '.join(market.market_trends)}\n"
                f"COMPETITIVE ADVANTAGES: {', '.join(competitive.competitive_advantages)}\n"
                f"MARKET GAPS: {', '.join(competitive.market_gaps)}\n"
                f"THREAT ASSESSMENT: {competitive.threat_assessment}\n\n"
                "Generate a comprehensive, specific SWOT analysis for this business."
            ),
        )

    def analyze(
        self,
        profile: BusinessProfile,
        market: MarketAnalysis,
        competitive: CompetitiveAnalysis,
    ) -> SWOTAnalysis:
        return self._call(profile, market, competitive)

    async def analyze_async(
        self,
        profile: BusinessProfile,
        market: MarketAnalysis,
        competitive: CompetitiveAnalysis,
    ) -> SWOTAnalysis:
        return await self._call_async(profile, market, competitive)


class BIReportWriterAgent:
    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    def _call(
        self,
        profile: BusinessProfile,
        market: MarketAnalysis,
        competitive: CompetitiveAnalysis,
        swot: SWOTAnalysis,
    ) -> BIReport:
        return self._llm.with_model(settings.agent_model_report_writer).invoke_structured(
            output_model=BIReport,
            system_prompt=(
                "You are a Senior Business Analyst at McKinsey & Company, writing an executive-level "
                "Business Intelligence Report. Your reports are read by C-suite executives and board "
                "members to make strategic decisions. You synthesize complex data into clear, "
                "actionable insights.\n\n"
                "REPORT STRUCTURE:\n"
                "1. Executive Summary (2-3 paragraphs):\n"
                "   - The business at a glance\n"
                "   - Key strategic findings\n"
                "   - Top 3 recommendations (hook the reader)\n\n"
                "2. Synthesize the business profile into a cohesive narrative\n"
                "3. Present market analysis with strategic implications\n"
                "4. Competitive landscape with key threats and opportunities\n"
                "5. SWOT summary highlighting critical items\n\n"
                "6. Recommendations (minimum 5):\n"
                "   - Each must be specific, actionable, and prioritized\n"
                "   - Include implementation considerations\n"
                "   - Reference SWOT/competitive insights where relevant\n\n"
                "7. Risk Factors (minimum 3):\n"
                "   - Identify the most material risks\n"
                "   - Assess likelihood and potential impact\n"
                "   - Suggest mitigation approaches\n\n"
                "WRITING STANDARDS:\n"
                "- Professional, concise, authoritative tone\n"
                "- No fluff or corporate jargon - every sentence adds value\n"
                "- Use specific data points and references from the analysis\n"
                "- Make explicit connections between SWOT items and recommendations\n"
                "- The report should stand alone - a reader can understand the business from it"
            ),
            user_prompt=(
                f"BUSINESS PROFILE:\n{profile.model_dump_json(indent=2)}\n\n"
                f"MARKET ANALYSIS:\n{market.model_dump_json(indent=2)}\n\n"
                f"COMPETITIVE ANALYSIS:\n{competitive.model_dump_json(indent=2)}\n\n"
                f"SWOT ANALYSIS:\n{swot.model_dump_json(indent=2)}\n\n"
                "Write a comprehensive, executive-level BI report synthesizing all the above."
            ),
        )

    async def _call_async(
        self,
        profile: BusinessProfile,
        market: MarketAnalysis,
        competitive: CompetitiveAnalysis,
        swot: SWOTAnalysis,
    ) -> BIReport:
        return await self._llm.with_model(settings.agent_model_report_writer).ainvoke_structured(
            output_model=BIReport,
            system_prompt=(
                "You are a Senior Business Analyst at McKinsey & Company, writing an executive-level "
                "Business Intelligence Report. Your reports are read by C-suite executives and board "
                "members to make strategic decisions. You synthesize complex data into clear, "
                "actionable insights.\n\n"
                "REPORT STRUCTURE:\n"
                "1. Executive Summary (2-3 paragraphs):\n"
                "   - The business at a glance\n"
                "   - Key strategic findings\n"
                "   - Top 3 recommendations (hook the reader)\n\n"
                "2. Synthesize the business profile into a cohesive narrative\n"
                "3. Present market analysis with strategic implications\n"
                "4. Competitive landscape with key threats and opportunities\n"
                "5. SWOT summary highlighting critical items\n\n"
                "6. Recommendations (minimum 5):\n"
                "   - Each must be specific, actionable, and prioritized\n"
                "   - Include implementation considerations\n"
                "   - Reference SWOT/competitive insights where relevant\n\n"
                "7. Risk Factors (minimum 3):\n"
                "   - Identify the most material risks\n"
                "   - Assess likelihood and potential impact\n"
                "   - Suggest mitigation approaches\n\n"
                "WRITING STANDARDS:\n"
                "- Professional, concise, authoritative tone\n"
                "- No fluff or corporate jargon - every sentence adds value\n"
                "- Use specific data points and references from the analysis\n"
                "- Make explicit connections between SWOT items and recommendations\n"
                "- The report should stand alone - a reader can understand the business from it"
            ),
            user_prompt=(
                f"BUSINESS PROFILE:\n{profile.model_dump_json(indent=2)}\n\n"
                f"MARKET ANALYSIS:\n{market.model_dump_json(indent=2)}\n\n"
                f"COMPETITIVE ANALYSIS:\n{competitive.model_dump_json(indent=2)}\n\n"
                f"SWOT ANALYSIS:\n{swot.model_dump_json(indent=2)}\n\n"
                "Write a comprehensive, executive-level BI report synthesizing all the above."
            ),
        )

    def write(
        self,
        profile: BusinessProfile,
        market: MarketAnalysis,
        competitive: CompetitiveAnalysis,
        swot: SWOTAnalysis,
    ) -> BIReport:
        return self._call(profile, market, competitive, swot)

    async def write_async(
        self,
        profile: BusinessProfile,
        market: MarketAnalysis,
        competitive: CompetitiveAnalysis,
        swot: SWOTAnalysis,
    ) -> BIReport:
        return await self._call_async(profile, market, competitive, swot)


class MarketingAnalysisAgent:
    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    def _call(self, profile: BusinessProfile, scraped: ScrapedContent) -> MarketingAnalysis:
        return self._llm.with_model(settings.agent_model_marketing).invoke_structured(
            output_model=MarketingAnalysis,
            system_prompt=(
                "You are a senior Marketing & SEO Strategist with expertise in digital marketing, "
                "brand strategy, and generative engine optimization (GEO). You have led marketing "
                "campaigns for Fortune 500 brands and high-growth startups.\n\n"
                "ANALYSIS FRAMEWORK:\n\n"
                "1. TARGET AUDIENCE PERSONAS (2-4 personas):\n"
                "   - Create named personas with demographic and psychographic profiles\n"
                "   - Identify their specific pain points related to the business's offerings\n"
                "   - Define their goals and what success looks like for them\n"
                "   - List the channels where they spend time (social, search, email, etc.)\n\n"
                "2. BRAND PERSONALITY:\n"
                "   - Identify the brand archetype (Creator, Sage, Hero, Outlaw, etc.)\n"
                "   - Define key personality traits (5-7 traits like 'innovative', 'trustworthy')\n"
                "   - Describe voice characteristics (formal/casual, authoritative/friendly)\n\n"
                "3. FAQ ANALYSIS:\n"
                "   - Generate 5-8 'People Also Ask' questions customers would search\n"
                "   - Provide concise, accurate answers based on the business's content\n\n"
                "4. CUSTOMER JOURNEY (4-5 stages):\n"
                "   - Map the journey from Awareness → Consideration → Decision → Retention → Advocacy\n"
                "   - Identify key touchpoints at each stage\n\n"
                "5. E-E-A-T EVALUATION:\n"
                "   - Experience: evidence of practical expertise\n"
                "   - Expertise: credentials, qualifications, depth of knowledge shown\n"
                "   - Authoritativeness: recognition, citations, partnerships\n"
                "   - Trustworthiness: transparency, reviews, security signals\n\n"
                "6. GEO TACTICS:\n"
                "   - Recommendations for optimizing content for AI-powered search engines\n"
                "   - Structured data, conversational content, entity optimization\n\n"
                "7. CALL TO ACTION:\n"
                "   - Primary CTA recommendation with placement and messaging\n\n"
                "REQUIREMENTS:\n"
                "- All insights must be specific to THIS business, not generic\n"
                "- Personas should feel real and research-based\n"
                "- E-E-A-T signals must reference actual content from the website"
            ),
            user_prompt=(
                f"COMPANY: {profile.company_name}\n"
                f"INDUSTRY: {profile.industry}\n"
                f"DESCRIPTION: {profile.description}\n"
                f"TAGLINE: {profile.tagline}\n"
                f"VALUE PROPOSITION: {profile.value_proposition}\n"
                f"TARGET AUDIENCE: {', '.join(profile.target_audience)}\n"
                f"OFFERINGS: {', '.join(profile.offerings)}\n"
                f"WEBSITE: {profile.website}\n\n"
                f"WEBSITE CONTENT:\n{scraped.text_content[:10000]}\n\n"
                "Generate a comprehensive marketing analysis and strategy for this business."
            ),
        )

    async def _call_async(self, profile: BusinessProfile, scraped: ScrapedContent) -> MarketingAnalysis:
        return await self._llm.with_model(settings.agent_model_marketing).ainvoke_structured(
            output_model=MarketingAnalysis,
            system_prompt=(
                "You are a senior Marketing & SEO Strategist with expertise in digital marketing, "
                "brand strategy, and generative engine optimization (GEO). You have led marketing "
                "campaigns for Fortune 500 brands and high-growth startups.\n\n"
                "ANALYSIS FRAMEWORK:\n\n"
                "1. TARGET AUDIENCE PERSONAS (2-4 personas):\n"
                "   - Create named personas with demographic and psychographic profiles\n"
                "   - Identify their specific pain points related to the business's offerings\n"
                "   - Define their goals and what success looks like for them\n"
                "   - List the channels where they spend time (social, search, email, etc.)\n\n"
                "2. BRAND PERSONALITY:\n"
                "   - Identify the brand archetype (Creator, Sage, Hero, Outlaw, etc.)\n"
                "   - Define key personality traits (5-7 traits like 'innovative', 'trustworthy')\n"
                "   - Describe voice characteristics (formal/casual, authoritative/friendly)\n\n"
                "3. FAQ ANALYSIS:\n"
                "   - Generate 5-8 'People Also Ask' questions customers would search\n"
                "   - Provide concise, accurate answers based on the business's content\n\n"
                "4. CUSTOMER JOURNEY (4-5 stages):\n"
                "   - Map the journey from Awareness → Consideration → Decision → Retention → Advocacy\n"
                "   - Identify key touchpoints at each stage\n\n"
                "5. E-E-A-T EVALUATION:\n"
                "   - Experience: evidence of practical expertise\n"
                "   - Expertise: credentials, qualifications, depth of knowledge shown\n"
                "   - Authoritativeness: recognition, citations, partnerships\n"
                "   - Trustworthiness: transparency, reviews, security signals\n\n"
                "6. GEO TACTICS:\n"
                "   - Recommendations for optimizing content for AI-powered search engines\n"
                "   - Structured data, conversational content, entity optimization\n\n"
                "7. CALL TO ACTION:\n"
                "   - Primary CTA recommendation with placement and messaging\n\n"
                "REQUIREMENTS:\n"
                "- All insights must be specific to THIS business, not generic\n"
                "- Personas should feel real and research-based\n"
                "- E-E-A-T signals must reference actual content from the website"
            ),
            user_prompt=(
                f"COMPANY: {profile.company_name}\n"
                f"INDUSTRY: {profile.industry}\n"
                f"DESCRIPTION: {profile.description}\n"
                f"TAGLINE: {profile.tagline}\n"
                f"VALUE PROPOSITION: {profile.value_proposition}\n"
                f"TARGET AUDIENCE: {', '.join(profile.target_audience)}\n"
                f"OFFERINGS: {', '.join(profile.offerings)}\n"
                f"WEBSITE: {profile.website}\n\n"
                f"WEBSITE CONTENT:\n{scraped.text_content[:10000]}\n\n"
                "Generate a comprehensive marketing analysis and strategy for this business."
            ),
        )

    def analyze(self, profile: BusinessProfile, scraped: ScrapedContent) -> MarketingAnalysis:
        return self._call(profile, scraped)

    async def analyze_async(self, profile: BusinessProfile, scraped: ScrapedContent) -> MarketingAnalysis:
        return await self._call_async(profile, scraped)


class MarketResearchAgent:
    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    async def research(self, market_query: str) -> MarketResearchResult:
        frameworks = self._detect_frameworks(market_query)
        return await self._llm.with_model(settings.agent_model_market_research).ainvoke_structured(
            output_model=MarketResearchResult,
            system_prompt=(
                "You are a Senior Market Research Analyst at a top-tier strategy consulting firm "
                "(McKinsey, Bain, BCG). You have 20+ years of experience conducting market research "
                "across all industries globally. You apply rigorous strategic frameworks to every "
                "analysis and demand data-backed conclusions.\n\n"
                "PERSONA:\n"
                "- You are known for your structured thinking and framework-driven analysis\n"
                "- You never make claims without evidence from your training data\n"
                "- You explicitly flag when data is uncertain or estimated\n"
                "- You think in terms of competitive dynamics, not just description\n\n"
                "YOUR TASK:\n"
                "Given a market query, conduct a multi-phase strategic analysis. "
                "Work through each phase sequentially, then compile your findings into the "
                "structured output format.\n\n"
                "PHASE 1 - PESTEL MACRO ANALYSIS:\n"
                "Scan the macro environment for factors affecting this market:\n"
                "- Political: regulations, trade policies, government stability\n"
                "- Economic: GDP trends, disposable income, inflation, employment\n"
                "- Social: demographics, cultural shifts, consumer behavior changes\n"
                "- Technological: innovation pace, R&D, automation, digital transformation\n"
                "- Environmental: sustainability pressure, climate regulations\n"
                "- Legal: compliance requirements, IP laws, antitrust\n\n"
                "PHASE 2 - PORTER'S FIVE FORCES:\n"
                "Assess industry structure and competitive intensity:\n"
                "1. Threat of New Entrants (barriers to entry, capital requirements, brand loyalty)\n"
                "2. Bargaining Power of Suppliers (concentration, switching costs)\n"
                "3. Bargaining Power of Buyers (price sensitivity, concentration)\n"
                "4. Threat of Substitutes (alternative solutions, switching costs)\n"
                "5. Competitive Rivalry (market concentration, exit barriers)\n\n"
                "PHASE 3 - MARKET SIZING (TAM/SAM/SOM):\n"
                "- Total Addressable Market (TAM): global revenue opportunity\n"
                "- Serviceable Addressable Market (SAM): segment you can reach\n"
                "- Serviceable Obtainable Market (SOM): realistic capture in 1-3 years\n"
                "- CAGR and growth trajectory\n\n"
                "PHASE 4 - SWOT SYNTHESIS:\n"
                "Identify top 3 per quadrant for this market as a whole:\n"
                "- Strengths: what works well in this market\n"
                "- Weaknesses: structural challenges or gaps\n"
                "- Opportunities: unmet needs, emerging trends, adjacencies\n"
                "- Threats: disruptive risks, regulatory headwinds, competitive pressure\n\n"
                "PHASE 5 - RESOURCE COMPILATION:\n"
                "Compile real, verifiable resources in these categories:\n"
                "1. KEY PLAYERS - Major companies (5-8) with URLs and descriptions\n"
                "2. NEWS SOURCES - Industry publications, analyst blogs (3-5)\n"
                "3. INDUSTRY WEBSITES - Trade associations, research firms (3-5)\n"
                "4. COMPETITOR SITES - Direct and indirect competitors (3-5)\n"
                "5. RESEARCH PAPERS - Academic papers, analyst reports (2-4)\n"
                "6. RELEVANT COMMUNITIES - Forums, groups, Reddit (2-4)\n\n"
                "OUTPUT REQUIREMENTS:\n"
                "- executive_summary: One sentence on current state + one sentence on key "
                "insight + one sentence on recommended action (3 sentences max)\n"
                "- key_insights: Top 3-5 synthesized findings from the framework analysis\n"
                "- recommendations: 3-5 concrete next steps for someone entering this market\n"
                "- risks_and_assumptions: Key uncertainties, data limitations, assumptions made\n"
                "- summary: 2-3 paragraph detailed market landscape description\n"
                "- market_size: Include TAM/SAM with sources where possible\n"
                "- growth_rate: CAGR or annual growth rate if known\n"
                "- Every URL must be a real, existing organization based on your training data\n"
                "- Type field values: 'website', 'newsletter', 'forum', 'publication', 'tool', 'community'\n"
                "- Relevance: explain WHY this resource matters for THIS specific market\n"
                "- If uncertain about a URL, omit it rather than fabricating"
            ),
            user_prompt=(
                f"MARKET TO RESEARCH: {market_query}\n\n"
                f"FRAMEWORKS TO APPLY: {', '.join(frameworks)}\n\n"
                "Analyze this market using all phases described above. "
                "Work through PESTEL, Porter's Five Forces, TAM/SAM/SOM sizing, and SWOT "
                "synthesis before compiling resources. Return the complete structured analysis."
            ),
        )

    def _detect_frameworks(self, market_query: str) -> list[str]:
        q = market_query.lower()
        frameworks = ["PESTEL", "Porter's Five Forces", "TAM/SAM/SOM", "SWOT"]
        if "trend" in q or "macro" in q or "future" in q:
            frameworks = ["PESTEL", "SWOT"]
        elif "competit" in q or "rival" in q or "player" in q:
            frameworks = ["Porter's Five Forces", "SWOT"]
        elif "size" in q or "opportunity" in q or "worth" in q:
            frameworks = ["TAM/SAM/SOM", "PESTEL"]
        return frameworks
