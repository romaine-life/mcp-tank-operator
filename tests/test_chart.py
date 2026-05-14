"""Regression tests for the Helm chart's service-account token contract."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_deployment_uses_default_service_account_token() -> None:
    deployment = (ROOT / "chart/templates/deployment.yaml").read_text()

    assert "SA_TOKEN_PATH" not in deployment
    assert "tank-operator-token" not in deployment
    assert "audience: tank-operator" not in deployment

