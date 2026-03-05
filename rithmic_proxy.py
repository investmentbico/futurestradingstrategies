"""
Proxy support for async_rithmic websocket connections.

This module monkey-patches websockets.connect() to tunnel through
an HTTP CONNECT proxy when the https_proxy env var is set.
Also patches the SSL context to work through TLS-inspecting proxies.
"""
import os
import ssl
import socket
import base64
import asyncio
import logging
from pathlib import Path
from urllib.parse import urlparse

import websockets
import async_rithmic.client

logger = logging.getLogger(__name__)


def _get_proxy_config():
    """Parse proxy configuration from environment."""
    proxy_url = os.environ.get('https_proxy') or os.environ.get('HTTPS_PROXY', '')
    if not proxy_url:
        return None
    parsed = urlparse(proxy_url)
    return {
        'host': parsed.hostname,
        'port': parsed.port,
        'username': parsed.username,
        'password': parsed.password,
    }


def create_proxy_tunnel(target_host, target_port):
    """Create a TCP socket tunneled through HTTP CONNECT proxy."""
    proxy = _get_proxy_config()
    if not proxy:
        return None

    logger.debug(f"Creating proxy tunnel to {target_host}:{target_port} via {proxy['host']}:{proxy['port']}")

    sock = socket.create_connection((proxy['host'], proxy['port']), timeout=30)
    sock.settimeout(30)

    # Build CONNECT request
    auth = base64.b64encode(
        f"{proxy['username']}:{proxy['password']}".encode()
    ).decode()

    connect_req = (
        f"CONNECT {target_host}:{target_port} HTTP/1.1\r\n"
        f"Host: {target_host}:{target_port}\r\n"
        f"Proxy-Authorization: Basic {auth}\r\n"
        f"User-Agent: async_rithmic/1.0\r\n"
        f"\r\n"
    )
    sock.sendall(connect_req.encode())

    # Read proxy response
    response = b''
    while b'\r\n\r\n' not in response:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("Proxy closed connection")
        response += chunk

    first_line = response.split(b'\r\n')[0]
    if b'200' not in first_line:
        sock.close()
        raise ConnectionError(f"Proxy CONNECT failed: {first_line.decode()}")

    sock.settimeout(None)  # Back to blocking with no timeout
    logger.debug(f"Proxy tunnel established to {target_host}:{target_port}")
    return sock


def patch_websockets_for_proxy():
    """
    Monkey-patch websockets.connect to route through HTTP proxy.

    This patches the low-level create_connection in the event loop to
    use our proxy tunnel when connecting to Rithmic servers.
    """
    proxy = _get_proxy_config()
    if not proxy:
        logger.debug("No proxy configured, skipping patch")
        return

    original_create_connection = asyncio.selector_events.BaseSelectorEventLoop.create_connection

    async def proxied_create_connection(self, protocol_factory, host=None, port=None,
                                         *, ssl=None, sock=None, **kwargs):
        # Only proxy connections to rithmic.com when no sock is already provided
        if sock is None and host and 'rithmic.com' in str(host):
            logger.info(f"Routing {host}:{port} through proxy tunnel")
            try:
                tunnel_sock = create_proxy_tunnel(host, port)
                if tunnel_sock:
                    kwargs.pop('server_hostname', None)
                    # Use the SSL context passed by async_rithmic (which has Rithmic's certs)
                    return await original_create_connection(
                        self, protocol_factory,
                        ssl=ssl,
                        sock=tunnel_sock,
                        server_hostname=host,
                        **kwargs
                    )
            except Exception as e:
                logger.error(f"Proxy tunnel failed: {e}")
                raise

        return await original_create_connection(
            self, protocol_factory, host, port, ssl=ssl, sock=sock, **kwargs
        )

    asyncio.selector_events.BaseSelectorEventLoop.create_connection = proxied_create_connection
    logger.info("Websocket proxy patch applied")

    # Also patch the SSL context to include system CAs (needed for TLS-inspecting proxies)
    original_setup_ssl = async_rithmic.client._setup_ssl_context

    def patched_setup_ssl():
        # Start with system CAs (handles proxy MITM certs)
        ssl_context = ssl.create_default_context()
        # Also load Rithmic's own certificate
        cert_path = Path(async_rithmic.__file__).parent / 'certificates' / 'rithmic_ssl_cert_auth_params'
        if cert_path.exists():
            ssl_context.load_verify_locations(cert_path)
        return ssl_context

    async_rithmic.client._setup_ssl_context = patched_setup_ssl
    logger.info("SSL context patch applied (system CAs + Rithmic certs)")
