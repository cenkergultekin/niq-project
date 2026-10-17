"""Provider completion semantics and local tool history; no provider requests."""
import json
from types import SimpleNamespace as NS

import pytest

from backend.core.utils import ValidationError
from backend.services.ai_adapters import anthropic_send, chatgpt_send, response_input


class Block(NS):
    def model_dump(self, **kwargs):
        return vars(self)


class Stream:
    def __init__(self, events):
        self.events = events
    def __enter__(self):
        return iter(self.events)
    def __exit__(self, *args):
        pass


def client(events):
    calls = []
    return NS(responses=NS(create=lambda **body: calls.append(body) or Stream(events))), calls


PARAMS = {"model": "account-model", "messages": [{"role": "system", "content": "Read-only"}, {"role": "user", "content": "Count"}],
          "max_completion_tokens": 2048, "temperature": 0.0}
TOOL = {"type": "function", "function": {"name": "run_sql", "description": "SELECT only", "parameters": {"type": "object"}}}


def test_chatgpt_requires_terminal_completed_and_preserves_reasoning_tools():
    output = [Block(type="reasoning", id="r1", encrypted_content="opaque", summary=[]),
              Block(type="function_call", call_id="c1", name="run_sql", arguments='{"sql":"SELECT 1"}', namespace="niq")]
    sdk, calls = client([NS(type="response.output_text.delta", delta="partial"),
                         NS(type="response.completed", response=NS(output=output))])
    result = chatgpt_send(sdk, {**PARAMS, "tools": [TOOL]})
    body = calls[0]
    assert body["store"] is False and body["stream"] is True
    assert "max_output_tokens" not in body and "temperature" not in body and "previous_response_id" not in body
    assert body["input"][0]["role"] == "developer" and body["tools"][0]["type"] == "namespace"
    message = result.choices[0].message
    assert message.tool_calls[0].id == "c1" and message.tool_calls[0].function.name == "run_sql"
    history = [*PARAMS["messages"], {"role": "assistant", "content": "", "_response_items": message.response_items},
               {"role": "tool", "tool_call_id": "c1", "content": '{"n":1}'}]
    items = response_input(history)
    assert items[2]["encrypted_content"] == "opaque" and items[3]["call_id"] == "c1"
    assert items[4] == {"type": "function_call_output", "call_id": "c1", "output": '{"n":1}'}


@pytest.mark.parametrize("terminal", [None, "response.failed", "response.incomplete", "error"])
def test_partial_text_is_never_a_success(terminal):
    events = [NS(type="response.output_text.delta", delta="partial SQL")]
    if terminal:
        events.append(NS(type=terminal, response=NS(error=NS(code="subscription_sharing_usage_limit_exceeded"))))
    sdk, calls = client(events)
    with pytest.raises(ValidationError):
        chatgpt_send(sdk, PARAMS)
    assert len(calls) == 1  # no fallback or billable retries


def test_completed_chatgpt_structured_text():
    output = [Block(type="message", role="assistant", content=[NS(type="output_text", text='{"sql":"SELECT 1"}')])]
    sdk, calls = client([NS(type="response.completed", response=NS(output=output))])
    schema = {"name": "sql", "strict": True, "schema": {"type": "object"}}
    result = chatgpt_send(sdk, {**PARAMS, "response_format": {"json_schema": schema}})
    assert json.loads(result.choices[0].message.content)["sql"] == "SELECT 1"
    assert calls[0]["text"]["format"] == {"type": "json_schema", **schema}


def test_completed_refusal_is_not_empty_success():
    output = [Block(type="message", role="assistant", content=[NS(type="refusal", refusal="No")])]
    sdk, _ = client([NS(type="response.completed", response=NS(output=output))])
    with pytest.raises(ValidationError):
        chatgpt_send(sdk, PARAMS)


@pytest.mark.parametrize("status,code,expected", [(429, "rate_limit_exceeded", "Kullanımı yönet"),
                                                 (403, "subscription_sharing_usage_unavailable", "Kullanımı yönet"),
                                                 (401, "invalid_token", "yeniden giriş")])
def test_chatgpt_http_errors_keep_correct_recovery(monkeypatch, status, code, expected):
    import httpx2
    from openai import APIStatusError

    from backend.services.ai_accounts import AccountStore
    from backend.services.ai_service import AIService, AIServiceError
    service = AIService(config={"provider": "chatgpt", "api_key": "synthetic", "account_id": "synthetic",
                                "base_url": "https://api.openai.com/v1", "model": "model", "max_output_tokens": 2048})
    monkeypatch.setattr(AccountStore, "chatgpt_credentials", lambda *a: {"access_token": "synthetic"})
    def fail(**body):
        response = httpx2.Response(status, request=httpx2.Request("POST", "https://api.openai.com/v1/responses"))
        raise APIStatusError("synthetic", response=response, body={"code": code})
    monkeypatch.setattr(service.client.responses, "create", fail)
    with pytest.raises(AIServiceError, match=expected):
        service._complete(PARAMS["messages"], None)


def test_anthropic_native_tools_results_and_thinking_signatures():
    sent = []
    blocks = [Block(type="thinking", thinking="protected reasoning", signature="signature"),
              Block(type="tool_use", id="c1", name="run_sql", input={"sql": "SELECT 1"})]
    sdk = NS(messages=NS(create=lambda **body: sent.append(body) or NS(content=blocks, stop_reason="tool_use")))
    result = anthropic_send(sdk, {**PARAMS, "tools": [TOOL]})
    message = result.choices[0].message
    assert sent[0]["system"] == "Read-only" and sent[0]["tools"][0]["input_schema"] == {"type": "object"}
    history = [*PARAMS["messages"], {"role": "assistant", "_anthropic_blocks": message.anthropic_blocks},
               {"role": "tool", "tool_call_id": "c1", "content": "one"},
               {"role": "tool", "tool_call_id": "c2", "content": "two"}]
    anthropic_send(sdk, {**PARAMS, "messages": history})
    assert sent[1]["messages"][1]["content"][0]["signature"] == "signature"
    assert len(sent[1]["messages"][2]["content"]) == 2
    assert sent[1]["messages"][2]["content"][0]["tool_use_id"] == "c1"


@pytest.mark.parametrize("reason", ["refusal", "max_tokens", "pause_turn", "model_context_window_exceeded"])
def test_anthropic_nonterminal_refusal_truncation_never_success(reason):
    sdk = NS(messages=NS(create=lambda **body: NS(content=[Block(type="text", text="partial")], stop_reason=reason)))
    with pytest.raises(ValidationError):
        anthropic_send(sdk, PARAMS)
