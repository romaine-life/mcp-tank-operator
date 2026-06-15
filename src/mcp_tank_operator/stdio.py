"""Stdio entrypoint — same tools, no HTTP transport.

For local development / testing where the caller is trusted. Identity is
the auth.romaine.life service-principal JWT, which in a session pod
arrives per-request in the X-Auth-Romaine-Token header (HTTP transport
only). Stdio has no such header, so identity-bearing tools raise
"service-principal authentication required" unless
MCP_TANK_OPERATOR_SERVICE_BEARER is set in the environment with a real
``role=service`` JWT — this entrypoint then binds it into SERVICE_BEARER
for the process so the tools can authenticate to a real orchestrator.

See romaine-life/tank-operator#486 for the rollout that retired the prior
IP-tail identity path (the old CALLER_POD_IP override this entrypoint
used to read no longer exists).
"""
import logging
import os

from mcp.server.fastmcp import FastMCP

from .caller import SERVICE_BEARER
from .client import TankClient
from .tools import register_tools


def main() -> None:
    logging.basicConfig(level=logging.INFO)

    # Allow a local override so the stdio server can be tested against a
    # real orchestrator from outside a session pod. Set once for the
    # process (stdio is single-caller); the HTTP transport instead binds
    # this per-request from X-Auth-Romaine-Token.
    bearer = os.environ.get("MCP_TANK_OPERATOR_SERVICE_BEARER", "").strip()
    if bearer:
        SERVICE_BEARER.set(bearer)

    mcp = FastMCP("tank-operator-mcp")
    client = TankClient()
    register_tools(mcp, client)
    mcp.run()


if __name__ == "__main__":
    main()
