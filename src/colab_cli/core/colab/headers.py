"""Header construction for Colab API requests."""

from __future__ import annotations


def build_colab_headers(
    access_token: str,
    *,
    tunnel: bool = False,
    proxy_token: str | None = None,
    xsrf_token: str | None = None,
) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "X-Colab-Client-Agent": "vscode",
    }
    if tunnel:
        headers["X-Colab-Tunnel"] = "Google"
    if proxy_token:
        headers["X-Colab-Runtime-Proxy-Token"] = proxy_token
    if xsrf_token:
        headers["X-Goog-Colab-Token"] = xsrf_token
    return headers

