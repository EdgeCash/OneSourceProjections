"""ntfy.sh push notifier — config gating and request shape."""

import project547.notify as notify


def test_send_noop_when_unconfigured(monkeypatch):
    monkeypatch.setattr(notify.config, "NTFY_TOPIC", lambda: None)
    assert notify.configured() is False
    # must not attempt any network call
    monkeypatch.setattr(notify.requests, "post",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("called")))
    assert notify.send("hi", title="t") is False


def test_send_posts_to_topic_with_headers(monkeypatch):
    monkeypatch.setattr(notify.config, "NTFY_TOPIC", lambda: "my-private-topic")
    monkeypatch.setattr(notify.config, "NTFY_SERVER", lambda: "https://ntfy.sh")
    monkeypatch.setattr(notify.config, "NTFY_TOKEN", lambda: None)
    captured = {}

    class _Resp:
        def raise_for_status(self): pass

    def _post(url, data=None, headers=None, timeout=None):
        captured.update(url=url, data=data, headers=headers)
        return _Resp()

    monkeypatch.setattr(notify.requests, "post", _post)
    ok = notify.send("body text", title="New card", tags=["dart"], priority="high",
                     click="https://example.com/?section=DFS")
    assert ok is True
    assert captured["url"] == "https://ntfy.sh/my-private-topic"
    assert captured["data"] == b"body text"
    assert captured["headers"]["Title"] == "New card"
    assert captured["headers"]["Tags"] == "dart"
    assert captured["headers"]["Priority"] == "high"
    assert captured["headers"]["Click"] == "https://example.com/?section=DFS"


def test_send_swallows_errors(monkeypatch):
    monkeypatch.setattr(notify.config, "NTFY_TOPIC", lambda: "t")
    def _boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(notify.requests, "post", _boom)
    assert notify.send("x") is False  # never raises


def test_send_includes_actions_header(monkeypatch):
    monkeypatch.setattr(notify.config, "NTFY_TOPIC", lambda: "t")
    monkeypatch.setattr(notify.config, "NTFY_TOKEN", lambda: None)
    cap = {}

    class _R:
        def raise_for_status(self): pass

    monkeypatch.setattr(notify.requests, "post",
                        lambda url, data=None, headers=None, timeout=None: cap.update(headers=headers) or _R())
    notify.send("body", actions="http, Played, https://x/y, method=POST")
    assert cap["headers"]["Actions"].startswith("http, Played")


def test_confirm_topic_default_suffix(monkeypatch):
    monkeypatch.setattr(notify.config, "NTFY_TOPIC", lambda: "mytopic")
    monkeypatch.setattr(notify.config, "NTFY_CONFIRM_TOPIC", lambda: None)
    assert notify.confirm_topic() == "mytopic-confirm"
    monkeypatch.setattr(notify.config, "NTFY_CONFIRM_TOPIC", lambda: "custom")
    assert notify.confirm_topic() == "custom"


def test_poll_parses_message_bodies(monkeypatch):
    monkeypatch.setattr(notify.config, "NTFY_SERVER", lambda: "https://ntfy.sh")
    monkeypatch.setattr(notify.config, "NTFY_TOKEN", lambda: None)

    class _R:
        text = ('{"event":"open"}\n'
                '{"event":"message","message":"played abc123"}\n'
                '{"event":"message","message":"skip def456"}\n')
        def raise_for_status(self): pass

    monkeypatch.setattr(notify.requests, "get",
                        lambda *a, **k: _R())
    bodies = notify.poll("topic-confirm")
    assert bodies == ["played abc123", "skip def456"]
