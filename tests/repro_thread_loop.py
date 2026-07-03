"""
Root cause: ChatNVIDIA.with_structured_output().invoke() inside 
TPE + new_event_loop + run_until_complete causes httpx connection pool
mixing between threads. API returns HTTP 200 with shorter body (~4KB vs ~11KB)
that with_structured_output can't parse, returning None.

Fix: use ainvoke_structured (fully async) throughout the pipeline.
"""

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor

logging.basicConfig(level=logging.WARNING)

from app.analysis.agents import BusinessProfileAgent, MarketAnalysisAgent
from app.llm.client import LLMClient
from app.models.bi import ScrapedContent, MarketAnalysis

SCRAPED = ScrapedContent(
    url="https://example.com", title="Example Domain",
    description="Example illustration domain",
    text_content="This domain is for use in illustrative examples in documents.",
    headings=["H1: Example Domain"], meta_keywords=["example"], links=[],
)

ITERATIONS = 8


async def run_agent():
    llm = LLMClient()
    profile_agent = BusinessProfileAgent(llm)
    market_agent = MarketAnalysisAgent(llm)
    r1 = profile_agent.analyze(SCRAPED)
    if not r1:
        return "FAIL_A1"
    r2 = market_agent.analyze(r1, SCRAPED)
    if not r2:
        return "FAIL_A2"
    return "PASS"


async def run_async():
    llm = LLMClient()
    r1 = await llm.ainvoke_structured(
        type(SCRAPED.__class__.__name__, (object,), {})(),
        "test",
        "test",
    )
