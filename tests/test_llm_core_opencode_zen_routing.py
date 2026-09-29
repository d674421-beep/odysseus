"""OpenCode Zen serves model families from different APIs
(https://opencode.ai/docs/zen/): Claude/Qwen -> /messages, GPT/Grok/Muse Spark
-> /responses, the rest -> /chat/completions. Other providers must not move."""
import json

import httpx
import pytest

from src import llm_core as L

ZEN = "https://opencode.ai/zen/v1"
AUTH = {"Authorization": "Bearer sk-zen"}
MSGS = [{"role": "system", "content": "be brief"}, {"role": "user", "content": "hi"}]


@pytest.fixture(autouse=True)
def _no_response_cache(monkeypatch):
    monkeypatch.setattr(L, "_get_cached_response", lambda key: None)
    monkeypatch.setattr(L, "_set_cached_response", lambda *a, **k: None)


@pytest.mark.parametrize("model,api", [
    ("claude-sonnet-5", "anthropic"),
    ("qwen3.8-flash", "anthropic"),
    ("gpt-5.4-nano", "responses"),
    ("grok-4.7", "responses"),
    ("muse-spark-1.3", "responses"),
    ("deepseek-v4-pro", None),
    ("glm-5.3", None),
    ("jev-1.13", None),
])
def test_zen_model_family_table(model, api):
    assert L._opencode_api(ZEN, model) == api


@pytest.mark.parametrize("model,api", [
    ("gpt-6-luna", "responses"),
    ("grok-4.7", "responses"),
    ("muse-spark-1.3-contributor", "responses"),
    ("qwen3.8-flash", None),
    ("glm-5.3-flash", None),
    ("deepseek-v4-pro", None),
])
def test_go_model_family_table(model, api):
    # Measured on Go: GPT/Grok/Muse -> 503 on /chat/completions, 200 on
    # /responses; Qwen/GLM answer on /chat/completions.
    assert L._opencode_api("https://opencode.ai/zen/go/v1", model) == api


def test_routing_only_applies_to_opencode():
    assert L._opencode_api("https://api.openai.com/v1", "gpt-5.4-nano") is None
    assert L._stream_target_url("https://api.openai.com/v1", "gpt-5.4-nano") == "https://api.openai.com/v1/chat/completions"
    assert L._stream_target_url("https://chatgpt.com/backend-api/codex", "gpt-5.5") == "https://chatgpt.com/backend-api/codex/responses"


def _capture_sync(monkeypatch, body):
    seen = {}

    def fake_post(url, headers, **kwargs):
        seen.update(url=url, headers=dict(headers), json=kwargs.get("json"))
        return httpx.Response(200, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(L, "httpx_post_kimi_aware", fake_post)
    return seen


def test_sync_zen_claude_uses_anthropic_messages(monkeypatch):
    seen = _capture_sync(monkeypatch, {"content": [{"type": "text", "text": "OK"}]})
    assert L.llm_call(ZEN, "claude-haiku-4-5", MSGS, max_tokens=16, headers=AUTH) == "OK"
    assert seen["url"] == "https://opencode.ai/zen/v1/messages"
    assert seen["headers"]["x-api-key"] == "sk-zen"
    assert seen["headers"]["x-opencode-session"].startswith("odysseus-")
    assert seen["json"]["system"] and seen["json"]["max_tokens"] == 16


def test_sync_zen_gpt_uses_responses(monkeypatch):
    seen = _capture_sync(monkeypatch, {"output": [
        {"type": "reasoning", "summary": []},
        {"type": "message", "content": [{"type": "output_text", "text": "OK"}]},
    ]})
    assert L.llm_call(ZEN, "gpt-5.4-nano", MSGS, max_tokens=16, headers=AUTH) == "OK"
    assert seen["url"] == "https://opencode.ai/zen/v1/responses"
    assert seen["headers"]["Authorization"] == "Bearer sk-zen"
    assert seen["json"]["instructions"] == "be brief"
    assert seen["json"]["max_output_tokens"] == 16


def test_sync_zen_deepseek_stays_on_chat_completions(monkeypatch):
    seen = _capture_sync(monkeypatch, {"choices": [{"message": {"content": "OK"}}]})
    assert L.llm_call(ZEN, "deepseek-v4-pro", MSGS, headers=AUTH) == "OK"
    assert seen["url"] == "https://opencode.ai/zen/v1/chat/completions"


def _mock_stream_client(monkeypatch, sse_lines):
    seen = {}

    def handler(request):
        seen.update(url=str(request.url), headers=dict(request.headers), json=json.loads(request.content))
        return httpx.Response(200, text="\n".join(sse_lines) + "\n",
                              headers={"content-type": "text/event-stream"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(L, "_get_http_client", lambda: client)
    return seen


async def _collect(url, model):
    text = []
    async for chunk in L.stream_llm(url, model, MSGS, max_tokens=32, headers=AUTH, session_id="s1"):
        for line in chunk.splitlines():
            if line.startswith("data:") and line[5:].strip() not in ("", "[DONE]"):
                data = json.loads(line[5:])
                if isinstance(data.get("delta"), str):
                    text.append(data["delta"])
    return "".join(text)


async def test_stream_zen_gpt_uses_responses(monkeypatch):
    seen = _mock_stream_client(monkeypatch, [
        'data: {"type":"response.output_text.delta","delta":"O"}',
        'data: {"type":"response.output_text.delta","delta":"K"}',
        'data: {"type":"response.completed","response":{"usage":{"input_tokens":3,"output_tokens":1}}}',
    ])
    assert await _collect(ZEN, "gpt-5.4-nano") == "OK"
    assert seen["url"] == "https://opencode.ai/zen/v1/responses"
    assert seen["json"]["stream"] is True and seen["json"]["max_output_tokens"] == 32
    assert seen["headers"]["x-opencode-session"].startswith("odysseus-")


async def test_stream_zen_qwen_uses_anthropic_messages(monkeypatch):
    seen = _mock_stream_client(monkeypatch, [
        'event: message_start',
        'data: {"type":"message_start","message":{"model":"qwen3.8-flash","usage":{"input_tokens":3}}}',
        'event: content_block_delta',
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"OK"}}',
        'event: message_stop',
        'data: {"type":"message_stop"}',
    ])
    assert await _collect(ZEN, "qwen3.8-flash") == "OK"
    assert seen["url"] == "https://opencode.ai/zen/v1/messages"
    assert seen["headers"]["x-api-key"] == "sk-zen"


async def test_stream_openai_is_unchanged(monkeypatch):
    seen = _mock_stream_client(monkeypatch, [
        'data: {"choices":[{"delta":{"content":"OK"}}]}',
        'data: [DONE]',
    ])
    assert await _collect("https://api.openai.com/v1", "gpt-5.4-nano") == "OK"
    assert seen["url"] == "https://api.openai.com/v1/chat/completions"
    assert "x-opencode-session" not in seen["headers"]
