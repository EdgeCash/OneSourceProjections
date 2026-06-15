"""Push notifications via ntfy.sh (https://ntfy.sh).

Subscribe to a private topic in the ntfy iOS app, set NTFY_TOPIC (and optionally
NTFY_SERVER / NTFY_TOKEN) as secrets, and the hourly job pushes a notification
whenever a new first-qualify DFS card appears. A no-op (never raises) when the
topic is unset, so local/CI runs without the secret just skip it.
"""

from __future__ import annotations

import json
import logging

import requests

from . import config

log = logging.getLogger(__name__)


def configured() -> bool:
    return bool(config.NTFY_TOPIC())


def confirm_topic() -> str | None:
    """The topic the Played/Skip buttons POST to (and the job polls). Defaults
    to '<topic>-confirm' so no extra secret is needed; override with
    NTFY_CONFIRM_TOPIC."""
    explicit = config.NTFY_CONFIRM_TOPIC()
    if explicit:
        return explicit
    base = config.NTFY_TOPIC()
    return f"{base}-confirm" if base else None


def topic_url(topic: str) -> str:
    server = (config.NTFY_SERVER() or "https://ntfy.sh").rstrip("/")
    return f"{server}/{topic}"


def send(message: str, *, title: str | None = None, priority: str | None = None,
         tags: list[str] | None = None, click: str | None = None,
         actions: str | None = None, timeout: float = 10.0) -> bool:
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
    if actions:
        headers["Actions"] = actions
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


def poll(topic: str, since: str = "12h", timeout: float = 15.0) -> list[str]:
    """Return the message bodies posted to ``topic`` since ``since`` (a unix ts
    or a duration like '12h'). Used to read Played/Skip button taps. Never
    raises — returns [] on any error or when unconfigured."""
    if not topic:
        return []
    server = (config.NTFY_SERVER() or "https://ntfy.sh").rstrip("/")
    headers = {}
    token = config.NTFY_TOKEN()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        resp = requests.get(f"{server}/{topic}/json", params={"poll": "1", "since": since},
                            headers=headers, timeout=timeout)
        resp.raise_for_status()
        bodies = []
        for line in resp.text.splitlines():
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("event") == "message" and msg.get("message"):
                bodies.append(msg["message"])
        return bodies
    except Exception as e:
        log.warning("ntfy poll failed: %s", e)
        return False or []
