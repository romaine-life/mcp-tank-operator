"""Regression: the stdio entrypoint imports cleanly and uses the post-#486
service-JWT identity model, not the retired CALLER_POD_IP pod-IP path."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import mcp_tank_operator.stdio as stdio  # noqa: E402  (the import is the regression)


def test_stdio_imports_and_exposes_service_bearer() -> None:
    # Before this fix the module raised ImportError pulling the deleted
    # CALLER_POD_IP symbol out of caller.py (removed in #486).
    assert callable(stdio.main)
    assert hasattr(stdio, "SERVICE_BEARER")
    assert not hasattr(stdio, "CALLER_POD_IP")


def test_stdio_source_drops_retired_pod_ip_override() -> None:
    src = (
        Path(__file__).resolve().parents[1] / "src/mcp_tank_operator/stdio.py"
    ).read_text()
    # The retired override env var must be gone (bare "CALLER_POD_IP" may
    # still appear in the docstring as history — that's fine); the JWT
    # override is the replacement dev affordance.
    assert "CALLER_POD_IP_OVERRIDE" not in src
    assert "MCP_TANK_OPERATOR_SERVICE_BEARER" in src
