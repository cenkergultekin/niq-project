"""Provider-native transports, normalized to NIQ's small chat/tool interface."""
import json
from types import SimpleNamespace as NS

from backend.core.utils import ValidationError


def completion(content, calls=None, finish="stop", response_items=None, anthropic_blocks=None):
    message = NS(content=content, tool_calls=calls or [], response_items=response_items, anthropic_blocks=anthropic_blocks)
    return NS(choices=[NS(message=message, finish_reason=finish)])


def response_input(messages):
    items = []
    for m in messages:
        if m.get("_response_items") is not None:
            items.extend(m["_response_items"])
            continue
        if m["role"] == "tool":
            items.append({"type": "function_call_output", "call_id": m["tool_call_id"], "output": m["content"]})
            continue
        if m.get("content"):
            items.append({"role": "developer" if m["role"] == "system" else m["role"], "content": m["content"]})
        for call in m.get("tool_calls", []):
            items.append({"type": "function_call", "call_id": call["id"], "name": call["function"]["name"],
                          "namespace": "niq", "arguments": call["function"]["arguments"]})
    return items


def chatgpt_send(client, params):
    body = {"model": params["model"], "input": response_input(params["messages"]), "store": False, "stream": True}
    schema = params.get("response_format", {}).get("json_schema")
    if schema:
        body["text"] = {"format": {"type": "json_schema", **schema}}
    if params.get("tools"):
        functions = [{"type": "function", **t["function"]} for t in params["tools"]]
        body["tools"] = [{"type": "namespace", "name": "niq", "description": "Read-only database analysis", "tools": functions}]
    completed = None
    streamed_output = {}
    with client.responses.create(**body) as stream:
        for event in stream:
            if event.type in ("response.failed", "response.incomplete", "error"):
                error = getattr(getattr(event, "response", None), "error", None)
                code = getattr(error, "code", None) or getattr(event, "code", "")
                if code in ("subscription_sharing_usage_limit_exceeded", "subscription_sharing_usage_unavailable"):
                    raise ValidationError("ChatGPT plan kullanımı şu anda uygun değil veya limiti doldu. "
                                          "Kullanımı yönet: https://chatgpt.com/settings/usage")
                raise ValidationError("ChatGPT yanıtı tamamlanmadı. Yeniden dene.")
            if event.type == "response.completed":
                completed = event.response
            elif event.type == "response.output_item.done":
                streamed_output[event.output_index] = event.item
    if completed is None:
        raise ValidationError("ChatGPT bağlantısı yanıt tamamlanmadan kesildi. Yeniden dene.")
    content, calls, output = [], [], []
    # Subscription streams may leave the terminal response.output empty. The
    # finalized items were already delivered in output_item.done events.
    # Never use those items unless the entire response completed successfully.
    items = completed.output or [streamed_output[index] for index in sorted(streamed_output)]
    for item in items:
        raw = item.model_dump(exclude_none=True) if hasattr(item, "model_dump") else dict(item)
        output.append(raw)
        if item.type == "message":
            if any(c.type == "refusal" for c in item.content):
                raise ValidationError("ChatGPT bu isteğe yanıt vermedi. Sorunu değiştirerek yeniden dene.")
            content.extend(c.text for c in item.content if c.type == "output_text")
        elif item.type == "function_call":
            calls.append(NS(id=item.call_id, type="function", function=NS(name=item.name, arguments=item.arguments)))
    return completion("".join(content), calls, response_items=output)


def anthropic_send(client, params):
    system, messages = [], []
    for m in params["messages"]:
        if m["role"] in ("system", "developer"):
            system.append(m["content"])
            continue
        if m["role"] == "tool":
            role, blocks = "user", [{"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}]
        elif m.get("_anthropic_blocks") is not None:
            role, blocks = m["role"], m["_anthropic_blocks"]
        else:
            role, blocks = m["role"], []
            if m.get("content"):
                blocks.append({"type": "text", "text": m["content"]})
            for call in m.get("tool_calls", []):
                blocks.append({"type": "tool_use", "id": call["id"], "name": call["function"]["name"],
                               "input": json.loads(call["function"]["arguments"])})
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"].extend(blocks)
        else:
            messages.append({"role": role, "content": blocks})
    body = {"model": params["model"], "max_tokens": params.get("max_completion_tokens", 2048),
            "messages": messages, "system": "\n\n".join(system)}
    if params.get("tools"):
        body["tools"] = [{"name": t["function"]["name"], "description": t["function"].get("description", ""),
                          "input_schema": t["function"]["parameters"]} for t in params["tools"]]
    schema = params.get("response_format", {}).get("json_schema")
    if schema:
        body["output_config"] = {"format": {"type": "json_schema", "schema": schema["schema"]}}
    response = client.messages.create(**body)
    if response.stop_reason not in ("end_turn", "tool_use"):
        raise ValidationError("Claude yanıtı tamamlanmadı. Modeli veya çıktı limitini kontrol et.")
    texts, calls = [], []
    for block in response.content:
        if block.type == "text":
            texts.append(block.text)
        elif block.type == "tool_use":
            calls.append(NS(id=block.id, type="function", function=NS(name=block.name, arguments=json.dumps(block.input))))
    blocks = [b.model_dump(exclude_none=True) for b in response.content]
    return completion("".join(texts), calls, anthropic_blocks=blocks)
