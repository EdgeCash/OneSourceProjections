"""Push notifications via ntfy.sh (https://ntfy.sh).

Subscribe to a private topic in the ntfy iOS app, set NTFY_TOPIC (and optionally
NTFY_SERVER / NTFY_TOKEN) as secrets, and the hourly job pushes a notification
whenever a new first-qualify DFS card appears. A no-op (never raises) when the
topic is unset, so local/CI runs without the secret just skip it.
"""

from __future__ import annotations

import logging

import requests

from . import config

log = logging.getLogger(__name__)


def configured() -> bool:
    return bool(config.NTFY_TOPIC())


def send(message: str, *, title: str | None = None, priority: str | None = None,
         tags: list[str] | None = None, click: str | None = None,
         timeout: float = 10.0) -> bool:
    """POST a notification to the configured ntfy topic. Returns True on a 2xx,
    False if unconfigured or on any error (logged, never raised)."""
    topic = config.NTFY_TOPIC()
    if not topic:
        log.info("ntfy not configured (NTFY_TOPIC unset) — skipping push")
        return False
    server = (config.NTFY_SERVER() or "https://ntfy.sh").rstrip("/")
    url = f"{server}/{topic}"
    # ntfy reads metadata from headers; the body is the message. Header values
    # must be latin-1 safe, so coerce the title to ASCII defensively.
    headers: dict[str, str] = {}
    if title:
        headers["Title"] = title.encode("ascii", "replace").decode("ascii")
    if priority:
        headers["Priority"] = priority
    if tags:
        headers["Tags"] = ",".join(tags)
    if click:
        headers["Click"] = click
    token = config.NTFY_TOKEN()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        resp = requests.post(url, data=message.encode("utf-8"),
                             headers=headers, timeout=timeout)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.warning("ntfy push failed: %s", e)
        return False
