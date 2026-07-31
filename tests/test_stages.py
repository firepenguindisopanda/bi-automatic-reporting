"""Tests for the canonical pipeline stage registry."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.pipeline.stages import (
    BI_STAGES,
    PIPELINES,
    JobStatus,
    StageStatus,
    stage_by_slug,
    stages_for,
)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class TestRegistry:
    @pytest.mark.parametrize("kind", sorted(PIPELINES))
    def test_slugs_unique(self, kind: str) -> None:
        slugs = [s.slug for s in stages_for(kind)]
        assert len(slugs) == len(set(slugs))

    @pytest.mark.parametrize("kind", sorted(PIPELINES))
    def test_seq_contiguous_and_ordered(self, kind: str) -> None:
        seqs = [s.seq for s in stages_for(kind)]
        assert seqs == list(range(len(seqs)))

    @pytest.mark.parametrize("kind", sorted(PIPELINES))
    def test_every_stage_has_display_text(self, kind: str) -> None:
        for stage in stages_for(kind):
            assert stage.display.strip()
            assert stage.label.strip()

    def test_bi_pipeline_shape(self) -> None:
        slugs = [s.slug for s in BI_STAGES]
        assert slugs[0] == "scrape"
        assert slugs[-1] == "deliver"
        # Scraping, rendering and delivery are mechanical; everything else is an agent.
        assert [s.slug for s in BI_STAGES if not s.uses_llm] == ["scrape", "render", "deliver"]

    def test_lookup_by_slug(self) -> None:
        assert stage_by_slug("bi", "swot").display == "SWOT Analysis"

    def test_lookup_rejects_unknown_slug(self) -> None:
        with pytest.raises(KeyError):
            stage_by_slug("bi", "nope")

    def test_lookup_rejects_unknown_kind(self) -> None:
        with pytest.raises(KeyError):
            stages_for("nope")


class TestStatusEnums:
    def test_statuses_are_plain_strings(self) -> None:
        # Persisted straight into TEXT columns, so the value must be the bare string.
        assert JobStatus.PARTIAL == "partial"
        assert StageStatus.SUCCEEDED == "succeeded"


class TestStagesEndpoint:
    def test_returns_bi_stages_in_order(self, client: TestClient) -> None:
        resp = client.get("/api/pipelines/bi/stages")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == len(BI_STAGES)
        assert [s["slug"] for s in data] == [s.slug for s in BI_STAGES]
        assert data[0]["label"] == "Scraping the site"

    def test_returns_market_research_stages(self, client: TestClient) -> None:
        resp = client.get("/api/pipelines/market_research/stages")
        assert resp.status_code == 200
        assert [s["slug"] for s in resp.json()] == ["macro", "sizing", "resources", "render"]

    def test_404_for_unknown_pipeline(self, client: TestClient) -> None:
        assert client.get("/api/pipelines/nonsense/stages").status_code == 404
