"""OpenCode Go rejects chat requests without x-opencode-session (HTTP 400
MissingSessionID), so the headers must be derived from the endpoint URL."""
from src.llm_core import (
    _apply_opencode_headers,
    _detect_provider,
    _provider_headers,
    _provider_label,
)


def _headers_for(url, messages, session_id=None):
    provider = _detect_provider(url)
    h = _provider_headers(provider)
    _apply_opencode_headers(h, provider, messages, session_id)
    return h


def test_detects_opencode_go_and_zen_by_path():
    assert _detect_provider("https://opencode.ai/zen/go/v1") == "opencode-go"
    assert _detect_provider("https://opencode.ai/zen/go/v1/chat/completions") == "opencode-go"
    assert _detect_provider("https://opencode.ai/zen/v1") == "opencode-zen"
    assert _detect_provider("https://opencode.ai.example.com/zen/go/v1") == "openai"
    assert _provider_label("https://opencode.ai/zen/go/v1") == "OpenCode Go"
    assert _provider_label("https://opencode.ai/zen/v1") == "OpenCode Zen"


def test_opencode_go_request_carries_stable_session_and_user_agent():
    url = "https://opencode.ai/zen/go/v1"
    first = _headers_for(url, [{"role": "system", "content": "s"}, {"role": "user", "content": "halo"}])
    later = _headers_for(url, [
        {"role": "user", "content": "halo"},
        {"role": "assistant", "content": "hai"},
        {"role": "user", "content": "lagi"},
    ])
    assert first["x-opencode-session"].startswith("odysseus-")
    assert first["x-opencode-session"] == later["x-opencode-session"]
    assert first["User-Agent"].startswith("odysseus/")


def test_session_id_wins_and_other_providers_untouched():
    url = "https://opencode.ai/zen/go/v1"
    a = _headers_for(url, [{"role": "user", "content": "x"}], session_id="s1")
    b = _headers_for(url, [{"role": "user", "content": "y"}], session_id="s1")
    assert a["x-opencode-session"] == b["x-opencode-session"]
    plain = _headers_for("https://api.openai.com/v1", [{"role": "user", "content": "x"}])
    assert "x-opencode-session" not in plain
    assert "User-Agent" not in plain
