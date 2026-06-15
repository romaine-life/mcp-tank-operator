"""Regression tests for the Helm chart's auth/identity contract.

Authorization is the auth.romaine.life service-principal JWT verified by
the orchestrator — NOT a kube-rbac-proxy sidecar gated on a per-caller
Kubernetes RBAC allowlist. These tests pin that boundary so the retired
allowlist (claude-session / the deleted hermes / dynamic slot SAs) cannot
silently return.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "chart"
TEMPLATES = CHART / "templates"


def test_deployment_projects_tank_operator_audience_token() -> None:
    deployment = (TEMPLATES / "deployment.yaml").read_text()

    assert "TANK_OPERATOR_SA_TOKEN_PATH" in deployment
    assert "value: /var/run/secrets/tank-operator/token" in deployment
    assert "name: tank-operator-sa-token" in deployment
    assert "audience: tank-operator" in deployment
    assert "expirationSeconds: 3600" in deployment


def test_kube_rbac_proxy_templates_are_gone() -> None:
    # The proxy ConfigMap/ClusterRole/RoleBinding and the TokenReview
    # delegation existed only to run the removed sidecar gate.
    assert not (TEMPLATES / "proxy-config.yaml").exists()
    assert not (TEMPLATES / "auth-delegator.yaml").exists()


def test_deployment_has_no_proxy_sidecar() -> None:
    deployment = (TEMPLATES / "deployment.yaml").read_text()

    # The unambiguous reintroduction signal is the proxy image; bare
    # "kube-rbac-proxy" prose in comments is allowed (and informative).
    assert "quay.io/brancz/kube-rbac-proxy" not in deployment
    assert "proxy-config" not in deployment
    # Readiness now hits the network-exposed app port directly.
    assert "path: /healthz" in deployment


def test_service_targets_the_app_port_not_a_proxy() -> None:
    service = (TEMPLATES / "service.yaml").read_text()

    assert "targetPort: upstream" in service
    assert "targetPort: proxy" not in service


def test_server_binds_pod_network_not_loopback() -> None:
    http = (ROOT / "src/mcp_tank_operator/http.py").read_text()

    assert 'host="0.0.0.0"' in http
    assert 'host="127.0.0.1"' not in http


def test_per_caller_rbac_allowlist_does_not_return() -> None:
    # Authorization is the auth.romaine.life JWT; no chart artifact may
    # reintroduce the per-SA invoker allowlist or its stale hermes subject.
    for path in CHART.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text()
        assert "hermes" not in text, f"stale hermes subject in {path}"
        assert "mcp-tank-operator-invoker" not in text, (
            f"per-caller invoker allowlist in {path}"
        )
        assert "quay.io/brancz/kube-rbac-proxy" not in text, (
            f"kube-rbac-proxy sidecar image in {path}"
        )
