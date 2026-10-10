from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, Field


def _flatten_market_size(value: Any) -> Any:
    """Accept the shape the model actually returns for a market size.

    The sizing prompt asks for TAM, SAM and SOM - three figures - while this
    field is a single line of display text, and every consumer renders it as
    one: the PDF template, the DOCX writer and the result page. So the model
    answers with an object, which failed validation on all three attempts and
    took the whole market-research run down with it.

    Flattening here keeps the three figures instead of arguing with the prompt.
    """
    if isinstance(value, dict):
        return " · ".join(f"{k.upper()}: {v}" for k, v in value.items() if v)
    return value


MarketSizeText = Annotated[str, BeforeValidator(_flatten_market_size)]
"""Display text for a market size, tolerant of a TAM/SAM/SOM object."""


class ScrapedContent(BaseModel):
    url: str
    title: str
    description: str
    headings: list[str] = Field(default_factory=list)
    text_content: str
    meta_keywords: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)
    error: str | None = None


class BusinessProfile(BaseModel):
    company_name: str
    tagline: str
    description: str
    industry: str
    offerings: list[str] = Field(default_factory=list)
    target_audience: list[str] = Field(default_factory=list)
    value_proposition: str
    contact_info: dict[str, str] = Field(default_factory=dict)
    social_links: list[str] = Field(default_factory=list)
    website: str = ""


class MarketAnalysis(BaseModel):
    target_market: str
    market_size: MarketSizeText
    market_trends: list[str] = Field(default_factory=list)
    customer_segments: list[str] = Field(default_factory=list)
    pricing_strategy: str
    market_positioning: str


class CompetitiveAnalysis(BaseModel):
    direct_competitors: list[dict[str, str]] = Field(default_factory=list)
    indirect_competitors: list[dict[str, str]] = Field(default_factory=list)
    competitive_advantages: list[str] = Field(default_factory=list)
    market_gaps: list[str] = Field(default_factory=list)
    threat_assessment: str


class SWOTAnalysis(BaseModel):
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    opportunities: list[str] = Field(default_factory=list)
    threats: list[str] = Field(default_factory=list)


class ReportSynthesis(BaseModel):
    """What the report writer produces: only the parts no other stage covers.

    The writer used to return a whole BIReport, re-writing the market,
    competitive and SWOT sections from scratch - and that re-written copy is
    what the PDF rendered. So the stages that were scored and reviewed were
    not the ones the reader saw, and the writer's reply ran long enough to be
    cut off at the token limit. Now it writes the synthesis and the report is
    assembled in code from the reviewed stage outputs (``build_report``).
    """

    executive_summary: str
    recommendations: list[str] = Field(default_factory=list)
    risk_factors: list[str] = Field(default_factory=list)


class BIReport(BaseModel):
    executive_summary: str
    business_profile: BusinessProfile
    market_analysis: MarketAnalysis
    competitive_analysis: CompetitiveAnalysis
    swot_analysis: SWOTAnalysis
    recommendations: list[str] = Field(default_factory=list)
    risk_factors: list[str] = Field(default_factory=list)


class TargetAudiencePersona(BaseModel):
    name: str = ""
    description: str = ""
    pain_points: list[str] = Field(default_factory=list)
    goals: list[str] = Field(default_factory=list)
    channels: list[str] = Field(default_factory=list)


class BrandPersonalityMatrix(BaseModel):
    archetype: str = ""
    traits: list[str] = Field(default_factory=list)
    voice_characteristics: str = ""


class FAQItem(BaseModel):
    question: str = ""
    answer: str = ""


class CustomerJourneyStage(BaseModel):
    stage: str = ""
    description: str = ""
    touchpoints: list[str] = Field(default_factory=list)


class EEATSignal(BaseModel):
    experience: list[str] = Field(default_factory=list)
    expertise: list[str] = Field(default_factory=list)
    authoritativeness: list[str] = Field(default_factory=list)
    trustworthiness: list[str] = Field(default_factory=list)


class GEOResult(BaseModel):
    tactic: str = ""
    description: str = ""


class MarketingAnalysis(BaseModel):
    personas: list[TargetAudiencePersona] = Field(default_factory=list)
    brand_personality: BrandPersonalityMatrix | None = None
    faq: list[FAQItem] = Field(default_factory=list)
    customer_journey: list[CustomerJourneyStage] = Field(default_factory=list)
    eeat: EEATSignal | None = None
    geo_tactics: list[GEOResult] = Field(default_factory=list)
    call_to_action: str = ""


class AnalysisArtifact(BaseModel):
    url: str
    scraped: ScrapedContent | None = None
    business_profile: BusinessProfile | None = None
    market_analysis: MarketAnalysis | None = None
    competitive_analysis: CompetitiveAnalysis | None = None
    swot_analysis: SWOTAnalysis | None = None
    report: BIReport | None = None
    marketing: MarketingAnalysis | None = None
    error: str | None = None


def build_report(
    synthesis: ReportSynthesis,
    profile: BusinessProfile,
    market: MarketAnalysis,
    competitive: CompetitiveAnalysis,
    swot: SWOTAnalysis,
) -> BIReport:
    """The report the reader gets, made of the stage outputs that were scored."""
    return BIReport(
        executive_summary=synthesis.executive_summary,
        business_profile=profile,
        market_analysis=market,
        competitive_analysis=competitive,
        swot_analysis=swot,
        recommendations=synthesis.recommendations,
        risk_factors=synthesis.risk_factors,
    )


class BISubmitRequest(BaseModel):
    url: str = Field(min_length=5, max_length=2000)
    email: str = Field(min_length=5, max_length=500)


class BISubmitResponse(BaseModel):
    job_id: str
    status: str = "processing"
    message: str


class BriefInput(BaseModel):
    session_id: str
    client_name: str = ""
    client_country: str = ""
    client_language: str = ""
    client_website: str = ""
    client_description: str = ""
    target_audience_personas: str = ""
    brand_personality_matrix: str = ""
    unique_value_proposition: str = ""
    people_ask: str = ""
    customer_journey: str = ""
    customer_persona_trait: str = ""
    eeat_signal_integration: str = ""
    geo_tactic: str = ""
    call_to_action: str = ""


class BriefResponse(BriefInput):
    id: int
    job_id: str
    created_at: str
    updated_at: str


class StageReport(BaseModel):
    """One stage's live state, as shown in the UI timeline."""

    stage: str
    display: str
    label: str
    seq: int
    status: str
    duration_ms: int | None = None
    model: str | None = None
    attempts: int = 0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    completeness: float | None = None
    confidence: float | None = None
    critic_note: str | None = None
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    error_kind: str | None = None


class JobProgress(BaseModel):
    """Everything needed to render a job's progress after a page refresh."""

    job_id: str
    kind: str = "bi"
    status: str
    error: str | None = None
    subject: str | None = None
    """What this job is about - the URL analysed, or the market researched.

    Sent so the page can name itself. The client used to rely on its own
    localStorage for this, so a job opened in another browser, in a private
    window, or from a shared link showed a bare job id.
    """
    stages: list[StageReport] = Field(default_factory=list)
    elapsed_ms: int = 0
    eta_ms: int | None = None
    quality: float | None = None
    confidence: float | None = None
    queue_position: int | None = None


class ResendResponse(BaseModel):
    job_id: str
    sent: bool
    detail: str = ""


class RetryResponse(BaseModel):
    job_id: str
    status: str


class StageDescriptor(BaseModel):
    """One pipeline stage, as advertised to the frontend."""

    slug: str
    display: str
    label: str
    seq: int
    uses_llm: bool


class MarketResource(BaseModel):
    name: str
    url: str
    description: str
    type: str = "website"
    relevance: str = ""


class MarketResearchResult(BaseModel):
    market_query: str
    executive_summary: str
    key_insights: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    risks_and_assumptions: list[str] = Field(default_factory=list)
    summary: str
    market_size: str = ""
    growth_rate: str = ""
    key_players: list[MarketResource] = Field(default_factory=list)
    news_sources: list[MarketResource] = Field(default_factory=list)
    industry_websites: list[MarketResource] = Field(default_factory=list)
    competitor_sites: list[MarketResource] = Field(default_factory=list)
    research_papers: list[MarketResource] = Field(default_factory=list)
    relevant_communities: list[MarketResource] = Field(default_factory=list)
    # The macro stage and the sizing SWOT were scored and reviewed and then
    # dropped on assembly, so the reader never saw them. Optional because
    # results stored before they were carried through have neither.
    pestel: "PESTELAnalysis | None" = None
    five_forces: "FiveForces | None" = None
    swot: SWOTAnalysis | None = None


class PESTELAnalysis(BaseModel):
    political: list[str] = Field(default_factory=list)
    economic: list[str] = Field(default_factory=list)
    social: list[str] = Field(default_factory=list)
    technological: list[str] = Field(default_factory=list)
    environmental: list[str] = Field(default_factory=list)
    legal: list[str] = Field(default_factory=list)


class FiveForces(BaseModel):
    threat_of_new_entrants: str = ""
    supplier_power: str = ""
    buyer_power: str = ""
    threat_of_substitutes: str = ""
    competitive_rivalry: str = ""


class MarketMacroAnalysis(BaseModel):
    """Stage 1 of market research: macro environment and industry structure."""

    pestel: PESTELAnalysis = Field(default_factory=PESTELAnalysis)
    five_forces: FiveForces = Field(default_factory=FiveForces)


class MarketSynthesis(BaseModel):
    """Stage 2: sizing plus the narrative synthesis, conditioned on the macro view."""

    executive_summary: str = ""
    summary: str = ""
    market_size: MarketSizeText = ""
    growth_rate: str = ""
    key_insights: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    risks_and_assumptions: list[str] = Field(default_factory=list)
    swot: SWOTAnalysis = Field(
        default_factory=lambda: SWOTAnalysis(
            strengths=[], weaknesses=[], opportunities=[], threats=[]
        )
    )


class MarketResources(BaseModel):
    """Stage 3: the verifiable resource lists."""

    key_players: list["MarketResource"] = Field(default_factory=list)
    news_sources: list["MarketResource"] = Field(default_factory=list)
    industry_websites: list["MarketResource"] = Field(default_factory=list)
    competitor_sites: list["MarketResource"] = Field(default_factory=list)
    research_papers: list["MarketResource"] = Field(default_factory=list)
    relevant_communities: list["MarketResource"] = Field(default_factory=list)


# Resolves the forward references to the macro models declared after it.
MarketResearchResult.model_rebuild()


class MarketResearchSubmitRequest(BaseModel):
    market_query: str = Field(min_length=3, max_length=500)


class MarketResearchSubmitResponse(BaseModel):
    job_id: str
    status: str = "processing"
    message: str


class MarketResearchStatusResponse(BaseModel):
    job_id: str
    status: str
    error: str | None = None


class MarketResearchResultResponse(BaseModel):
    job_id: str
    status: str
    result: MarketResearchResult | None = None
    error: str | None = None
