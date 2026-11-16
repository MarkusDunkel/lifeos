"""Mount the MCP server into the FastAPI app under a secret path."""

from __future__ import annotations

import logging

from fastapi import FastAPI
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings


class MaskPath(logging.Filter):
    """Keep the secret path token out of the access log."""

    def __init__(self, secret: str) -> None:
        super().__init__()
        self.secret = secret

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                a.replace(self.secret, "***") if isinstance(a, str) else a for a in record.args
            )
        if isinstance(record.msg, str) and self.secret in record.msg:
            record.msg = record.msg.replace(self.secret, "***")
        return True


def mount_mcp(app: FastAPI, server: MCPServer, token: str) -> None:
    """Serve ``server`` at ``/mcp/<token>/`` (stateless Streamable HTTP, JSON responses).

    The unguessable path is the only protection until OAuth is added; DNS rebinding
    protection is meant for servers on localhost and is off here (Caddy sets the host).
    """
    asgi = server.streamable_http_app(
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    app.mount(f"/mcp/{token}", asgi, name="mcp")
    mask = MaskPath(token)
    for name in ("uvicorn.access", "uvicorn.error", "uvicorn"):
        logger = logging.getLogger(name)
        if not any(isinstance(f, MaskPath) for f in logger.filters):
            logger.addFilter(mask)
