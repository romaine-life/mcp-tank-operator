"""Stdio entrypoint — same tools, no kube-rbac-proxy auth layer.

For local development / testing where the caller is trusted. Pod IP
resolution is a no-op here (no X-Forwarded-For in stdio mode), so all
tools will raise "could not identify caller from pod IP" unless
CALLER_POD_IP_OVERRIDE is set in the environment.
"""
import logging
import os

from mcp.server.fastmcp import FastMCP

from .caller import CALLER_POD_IP
from .client import TankClient
from .tools import register_tools


def main() -> None:
    logging.basicConfig(level=logging.INFO)

    # Allow a local override so the stdio server can be tested against a
    # real orchestrator from outside a session pod.
    override_ip = os.environ.get("CALLER_POD_IP_OVERRIDE", "").strip()
    if override_ip:
        CALLER_POD_IP.set(override_ip)

    mcp = FastMCP("tank-operator-mcp")
    client = TankClient()
    register_tools(mcp, client)
    mcp.run()


if __name__ == "__main__":
    main()
