"""Tests for serving the built frontend.

The catch-all route reads files off disk using a path the caller supplies, so
the containment check is the only thing standing between a request and every
file the process can open. These tests exist because it was once missing:
``GET /..%2f.env`` returned the deployment's secrets.
"""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.main as main


@pytest.fixture
def static_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A stand-in frontend build, with a secret sitting next to it."""
    root = tmp_path / "static"
    root.mkdir()
    (root / "index.html").write_text("<html>spa</html>")
    (root / "app.css").write_text("body{}")
    (tmp_path / "secret.txt").write_text("TOP-SECRET")
    monkeypatch.setattr(main, "static_dir", root.resolve())
    return root


class TestSafeStaticFile:
    def test_serves_a_file_inside_the_build(self, static_root: Path) -> None:
        assert main.safe_static_file("app.css") == static_root / "app.css"

    def test_missing_file_is_not_an_error(self, static_root: Path) -> None:
        assert main.safe_static_file("nope.css") is None

    def test_the_directory_itself_is_not_a_file(self, static_root: Path) -> None:
        assert main.safe_static_file("") is None

    @pytest.mark.parametrize(
        "path",
        [
            "../secret.txt",
            "./../secret.txt",
            "a/../../secret.txt",
            "../../../../../../../etc/passwd",
        ],
    )
    def test_traversal_is_refused(self, static_root: Path, path: str) -> None:
        assert main.safe_static_file(path) is None

    def test_absolute_path_is_refused(self, static_root: Path) -> None:
        """`Path("/a") / "/etc/passwd"` is `/etc/passwd` - the join alone is no guard."""
        assert main.safe_static_file(str(static_root.parent / "secret.txt")) is None

    def test_symlink_out_of_the_build_is_refused(self, static_root: Path) -> None:
        """Resolving before the check is what catches this; comparing strings would not."""
        (static_root / "escape.txt").symlink_to(static_root.parent / "secret.txt")
        assert main.safe_static_file("escape.txt") is None


class TestSpaCatchAll:
    async def test_api_paths_are_left_alone(self, static_root: Path) -> None:
        assert (await main.spa_catch_all("api/bi/submit")).status_code == 404

    async def test_unknown_path_falls_back_to_the_spa(self, static_root: Path) -> None:
        resp = await main.spa_catch_all("jobs/abc123")
        assert resp.status_code == 200
        assert resp.body == b"<html>spa</html>"

    async def test_served_file_carries_a_content_type(self, static_root: Path) -> None:
        """`nosniff` is set globally, so a missing Content-Type means a refused asset."""
        resp = await main.spa_catch_all("app.css")
        assert resp.headers["content-type"].startswith("text/css")


class TestTraversalOverHttp:
    """The decoded path is what reaches the handler, which is how the hole opened.

    A request for ``/..%2fsecret.txt`` arrives as ``../secret.txt`` - the router
    normalises literal ``../`` but not its percent-encoded form.
    """

    @pytest.fixture
    def client(self, static_root: Path) -> TestClient:
        probe = FastAPI()
        probe.get("/{path:path}")(main.spa_catch_all)
        return TestClient(probe)

    @pytest.mark.parametrize(
        "path",
        [
            "/..%2fsecret.txt",
            "/%2e%2e/secret.txt",
            "/..%2f..%2fsecret.txt",
            "/..%2f..%2f..%2f..%2f..%2f..%2fetc%2fpasswd",
        ],
    )
    def test_encoded_traversal_returns_the_spa_not_the_file(
        self, client: TestClient, path: str
    ) -> None:
        resp = client.get(path)
        assert resp.status_code == 200
        assert "TOP-SECRET" not in resp.text
        assert "root:x:0:0" not in resp.text
        assert resp.text == "<html>spa</html>"
