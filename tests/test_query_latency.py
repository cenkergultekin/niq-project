"""Independent sources overlap; time budgets do not leak between requests."""
import threading
import time
from types import SimpleNamespace as NS

import pytest

from backend.services import ai_service, workspace
from backend.services.ai_adapters import chatgpt_send
from backend.services.ai_service import QUERY_DEADLINE, AIServiceError, query_budget, remaining_query_time
from backend.services.query_service import ConversationStore
from tests.test_ai_adapters import PARAMS, client


def test_workspace_sources_overlap_and_keep_source_order(monkeypatch):
    barrier = threading.Barrier(2, timeout=2)
    deadlines = []
    def run(connector, *args):
        deadlines.append(QUERY_DEADLINE.get())
        barrier.wait()
        return {"success": True, "results": [{"n": connector}], "sql": "SELECT 1"}, 200
    monkeypatch.setattr(workspace, "run_question", run)
    scope = {key: {"connector": i, "tables": ["orders"], "label": key} for i, key in enumerate(["first", "second"])}
    ai = NS(pick_databases=lambda *a: ["second", "first"])
    result, status = workspace.query_workspace(scope, ai, None, None, ConversationStore(), "count", "conversation")
    assert status == 200 and [source["source"] for source in result["sources"]] == ["second", "first"]
    assert deadlines[0] == deadlines[1] and deadlines[0] > time.monotonic()
    assert QUERY_DEADLINE.get() is None


def test_expired_query_does_not_start_another_provider_call():
    token = QUERY_DEADLINE.set(time.monotonic() - 1)
    try:
        with pytest.raises(AIServiceError, match="süre sınırına"):
            remaining_query_time()
    finally:
        QUERY_DEADLINE.reset(token)


def test_nested_budget_preserves_deadline_and_resets_after_error():
    values = []
    @query_budget
    def inner():
        values.append(QUERY_DEADLINE.get())
        raise RuntimeError("synthetic")
    @query_budget
    def outer():
        values.append(QUERY_DEADLINE.get())
        inner()
    with pytest.raises(RuntimeError):
        outer()
    assert values[0] == values[1] and QUERY_DEADLINE.get() is None


def test_stream_cannot_keep_query_alive_with_endless_deltas(monkeypatch):
    from backend.core.utils import ValidationError
    sdk, _ = client([NS(type="response.output_text.delta", delta="partial")])
    monkeypatch.setattr("backend.services.ai_adapters.time.monotonic", lambda: 20)
    with pytest.raises(ValidationError, match="süre sınırına"):
        chatgpt_send(sdk, PARAMS, deadline=10)


def test_query_provider_timeout_uses_remaining_budget_without_auto_retries(monkeypatch):
    service = ai_service.AIService(config={"provider": "openai", "api_key": "synthetic", "model": "model",
                                          "base_url": "https://api.openai.com/v1", "max_output_tokens": 2048})
    options = []
    fake = NS(chat=NS(completions=NS(create=lambda **kwargs: "completed")))
    monkeypatch.setattr(service.client, "with_options", lambda **kwargs: options.append(kwargs) or fake)
    token = QUERY_DEADLINE.set(time.monotonic() + 2)
    try:
        assert service._send({}) == "completed"
    finally:
        QUERY_DEADLINE.reset(token)
    assert 0 < options[0]["timeout"] <= 2 and options[0]["max_retries"] == 0
