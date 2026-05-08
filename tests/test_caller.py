"""Caller pod IP extraction.

Mirrors the same XFF-tail logic as mcp-github's caller.py: kube-rbac-proxy
appends the immediate peer (the session pod's IP) right-most, so we trust
the right-most entry. The session pod can spoof earlier hops in the chain
all it likes; the right-most entry was added by *our* fronting proxy from
the network-layer source IP.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mcp_tank_operator.caller import (  # noqa: E402
    CALLER_POD_IP,
    current_caller_pod_ip,
    extract_source_pod_ip,
)


def test_extract_source_pod_ip_picks_last_xff_hop() -> None:
    assert extract_source_pod_ip("10.244.1.94", peer_ip="127.0.0.1") == "10.244.1.94"


def test_extract_source_pod_ip_takes_last_when_multiple_hops() -> None:
    assert (
        extract_source_pod_ip("8.8.8.8, 10.244.1.94", peer_ip="127.0.0.1")
        == "10.244.1.94"
    )


def test_extract_source_pod_ip_strips_whitespace() -> None:
    assert (
        extract_source_pod_ip("8.8.8.8 ,   10.244.1.94  ", peer_ip="127.0.0.1")
        == "10.244.1.94"
    )


def test_extract_source_pod_ip_falls_back_to_peer_when_no_header() -> None:
    assert extract_source_pod_ip(None, peer_ip="10.244.1.50") == "10.244.1.50"


def test_extract_source_pod_ip_returns_none_when_nothing() -> None:
    assert extract_source_pod_ip(None, peer_ip=None) is None


def test_extract_source_pod_ip_falls_back_when_xff_empty_string() -> None:
    assert extract_source_pod_ip("", peer_ip="10.244.1.50") == "10.244.1.50"


def test_current_caller_pod_ip_default_is_none() -> None:
    """ContextVar default must be None — that's what tools use to detect the
    'caller unknown' case and surface the actionable error."""
    token = CALLER_POD_IP.set(None)
    try:
        assert current_caller_pod_ip() is None
    finally:
        CALLER_POD_IP.reset(token)


def test_current_caller_pod_ip_round_trips() -> None:
    token = CALLER_POD_IP.set("10.0.0.42")
    try:
        assert current_caller_pod_ip() == "10.0.0.42"
    finally:
        CALLER_POD_IP.reset(token)
