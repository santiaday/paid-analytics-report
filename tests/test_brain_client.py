import json

import pytest

from brain_client import parse_tool_result, _parse_body


# ---- parse_tool_result: the FastMCP result-unwrapping (all branches) ----
def test_parses_text_content_json():
    raw = {"content": [{"type": "text", "text": json.dumps({"rows": [{"a": 1}], "row_count": 1})}]}
    assert parse_tool_result(raw) == {"rows": [{"a": 1}], "row_count": 1}


def test_unwraps_result_envelope():
    inner = {"rows": [], "row_count": 0}
    raw = {"content": [{"type": "text", "text": json.dumps({"result": json.dumps(inner)})}]}
    assert parse_tool_result(raw) == inner


def test_is_error_raises_with_human_message():
    raw = {"isError": True, "content": [{"type": "text", "text": "boom: bad metric name"}]}
    with pytest.raises(RuntimeError, match="brain tool error: boom"):
        parse_tool_result(raw)


def test_tool_error_field_raises():
    raw = {"content": [{"type": "text", "text": json.dumps({"error": {"code": "E1", "message": "nope"}})}]}
    with pytest.raises(RuntimeError, match=r"brain tool error \[E1\]: nope"):
        parse_tool_result(raw)


def test_non_json_text_content_raises():
    raw = {"content": [{"type": "text", "text": "<html>not json</html>"}]}
    with pytest.raises(RuntimeError, match="non-JSON content"):
        parse_tool_result(raw)


def test_structured_content_fallback():
    raw = {"structuredContent": {"rows": [{"x": 1}], "row_count": 1}}
    assert parse_tool_result(raw)["row_count"] == 1


def test_no_content_raises():
    with pytest.raises(RuntimeError, match="no content"):
        parse_tool_result({})


# ---- _parse_body: JSON-RPC over buffered JSON and SSE ----
def test_parse_body_buffered_json():
    text = json.dumps({"jsonrpc": "2.0", "id": 7, "result": {"ok": True}})
    assert _parse_body("application/json", text, 7) == {"ok": True}


def test_parse_body_sse_picks_matching_id():
    text = (
        "event: message\n"
        'data: {"jsonrpc":"2.0","id":1,"result":{"first":true}}\n\n'
        'data: {"jsonrpc":"2.0","id":2,"result":{"second":true}}\n\n'
    )
    assert _parse_body("text/event-stream", text, 2) == {"second": True}


def test_parse_body_json_error_raises():
    text = json.dumps({"jsonrpc": "2.0", "id": 3, "error": {"message": "kaboom"}})
    with pytest.raises(RuntimeError, match="mcp error: kaboom"):
        _parse_body("application/json", text, 3)


def test_parse_body_non_json_raises():
    with pytest.raises(RuntimeError, match="non-JSON response"):
        _parse_body("text/html", "<html/>", 1)
