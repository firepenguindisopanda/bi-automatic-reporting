from pydantic import BaseModel, Field


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
    market_size: str
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


class BIJobStatus(BaseModel):
    job_id: str
    status: str
    progress: list[dict[str, str]] = Field(default_factory=list)
    error: str | None = None
