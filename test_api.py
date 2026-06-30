"""
Integration tests for the URL Shortener API.

These tests run against a LIVE stack (real Postgres, real Redis, real API)
rather than mocking everything out. This catches integration bugs that
unit tests with mocks would miss — like the route-ordering bug we found
manually (/health being shadowed by /{short_code}).

Run locally with:
    docker compose up -d
    pip install pytest httpx
    pytest tests/ -v

In CI, the GitHub Actions workflow spins up the full stack before running
this file.
"""

import time
import uuid

import httpx
import pytest

BASE_URL = "http://localhost:8000"


@pytest.fixture(scope="session", autouse=True)
def wait_for_api():
    """Poll /health until the API and its dependencies are ready, or fail after 30s."""
    deadline = time.time() + 30
    last_error = None

    while time.time() < deadline:
        try:
            response = httpx.get(f"{BASE_URL}/health", timeout=2)
            if response.status_code == 200:
                return
        except httpx.RequestError as exc:
            last_error = exc
        time.sleep(1)

    pytest.fail(f"API did not become healthy within 30s. Last error: {last_error}")


def test_health_check_returns_200():
    """The /health endpoint should report healthy status with both dependencies up."""
    response = httpx.get(f"{BASE_URL}/health")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "healthy"
    assert body["postgres"] is True
    assert body["redis"] is True


def test_shorten_creates_a_short_url():
    """POST /shorten should return a 201 with a short_code and short_url."""
    unique_url = f"https://example.com/test-{uuid.uuid4()}"

    response = httpx.post(f"{BASE_URL}/shorten", json={"url": unique_url})

    assert response.status_code == 201
    body = response.json()
    assert "short_code" in body
    assert "short_url" in body
    assert len(body["short_code"]) > 0
    assert body["short_url"].endswith(body["short_code"])


def test_shorten_is_idempotent():
    """Shortening the same URL twice should return the same short_code, not a duplicate."""
    unique_url = f"https://example.com/idempotent-{uuid.uuid4()}"

    first_response = httpx.post(f"{BASE_URL}/shorten", json={"url": unique_url})
    second_response = httpx.post(f"{BASE_URL}/shorten", json={"url": unique_url})

    assert first_response.json()["short_code"] == second_response.json()["short_code"]


def test_redirect_follows_to_original_url():
    """GET /{short_code} should redirect (302) to the original URL."""
    unique_url = f"https://example.com/redirect-test-{uuid.uuid4()}"

    shorten_response = httpx.post(f"{BASE_URL}/shorten", json={"url": unique_url})
    short_code = shorten_response.json()["short_code"]

    # follow_redirects=False so we can inspect the redirect itself,
    # rather than httpx silently following it to example.com
    redirect_response = httpx.get(
        f"{BASE_URL}/{short_code}", follow_redirects=False
    )

    assert redirect_response.status_code == 302
    assert redirect_response.headers["location"] == unique_url


def test_redirect_with_unknown_code_returns_404():
    """A short code that was never created should return 404, not crash."""
    response = httpx.get(f"{BASE_URL}/does-not-exist-{uuid.uuid4()}")
    assert response.status_code == 404


def test_shorten_rejects_invalid_url():
    """Sending a non-URL string should be rejected by Pydantic validation, not crash."""
    response = httpx.post(f"{BASE_URL}/shorten", json={"url": "not-a-valid-url"})
    assert response.status_code == 422


def test_health_endpoint_is_not_shadowed_by_short_code_route():
    """
    Regression test for the route-ordering bug:
    /health must resolve to the health check handler, not be treated
    as a short_code lookup by GET /{short_code}.
    """
    response = httpx.get(f"{BASE_URL}/health")
    assert response.status_code in (200, 503)  # never 404
    assert "postgres" in response.json() or "detail" in response.json()
