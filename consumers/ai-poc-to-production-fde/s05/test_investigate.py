import json

import investigate
import pytest


class FakeConverse:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        reply = next(self.replies)
        if isinstance(reply, BaseException):
            raise reply
        return reply


def test_parse_only_completed_json_lines():
    raw = '''=== success: HTTP 200 ===
Request input:
{"sku":"SKU-RED-01","quantity":2}
HTTP response:
{"requestId":"req-1"}
Matching CloudWatch app JSON log:
{"event":"request_complete","requestId":"req-1","status":200,"durationMs":12.5}
'''
    assert investigate.parse_run_evidence(raw) == {
        "success": {"event": "request_complete", "requestId": "req-1", "status": 200, "durationMs": 12.5}
    }


def test_search_returns_only_selected_actual_record():
    evidence = {
        "success": {"event": "request_complete", "requestId": "req-1", "status": 200, "durationMs": 12},
        "validation": {"event": "request_complete", "requestId": "req-2", "status": 400, "durationMs": 3},
    }
    assert investigate.search_run_logs("req-1", evidence) == {"app_log": evidence["success"]}


def test_investigation_makes_two_calls_and_keeps_human_notes_out_of_prompts():
    first = {"stopReason": "tool_use", "output": {"message": {"role": "assistant", "content": [{
        "toolUse": {"toolUseId": "tool-1", "name": investigate.TOOL_NAME, "input": {"selection": "only"}}
    }] }}, "usage": {"inputTokens": 10, "outputTokens": 5}}
    final = {"stopReason": "end_turn", "output": {"message": {"content": [{"text": "Observed status 200; cause is not established."}]}}, "usage": {"inputTokens": 20, "outputTokens": 8}}
    client = FakeConverse([first, final])
    budget = []
    def record_budget(*args, **kwargs):
        budget.append((args, kwargs))
    human = {
        "request_id": "req-1", "query": "What does this log show?", "findings": "PRIVATE HUMAN NOTES",
        "evidence_request_ids": ["req-1"], "uncertainty": "unknown", "started_at": "", "completed_at": "",
    }
    evidence = {"success": {"event": "request_complete", "requestId": "req-1", "status": 200, "durationMs": 12}}
    result = investigate.run_investigation(client, human, evidence, call_budget=record_budget)
    assert len(client.calls) == 2
    assert len(budget) == 2
    assert budget[0] == (("main",), {"request_id": "req-1", "start_investigation": True})
    assert budget[1] == (("main",), {"request_id": "req-1"})
    assert client.calls[0]["toolConfig"]["toolChoice"] == {"tool": {"name": "search_run_logs"}}
    assert "toolChoice" not in client.calls[1]["toolConfig"]
    assert result["ai_answer"] == "Observed status 200; cause is not established."
    assert "PRIVATE HUMAN NOTES" not in str(client.calls)
    assert "injected_failure" not in str(client.calls)
    assert result["first_response_usage"] == first["usage"]
    assert result["final_response_usage"] == final["usage"]
    assert result["tool_result"]["app_log"] == evidence["success"]


def test_investigation_stops_on_wrong_tool_without_retry():
    first = {"stopReason": "tool_use", "output": {"message": {"content": [{
        "toolUse": {"toolUseId": "tool-1", "name": "other_tool", "input": {"selection": "only"}}
    }]}}}
    client = FakeConverse([first])
    budget = []
    human = {"request_id": "req-1", "query": "Question", "findings": "findings", "evidence_request_ids": ["req-1"], "uncertainty": "", "started_at": "", "completed_at": ""}
    evidence = {"success": {"event": "request_complete", "requestId": "req-1", "status": 200, "durationMs": 12}}
    try:
        investigate.run_investigation(client, human, evidence, call_budget=lambda *args, **kwargs: budget.append((args, kwargs)))
    except ValueError as exc:
        assert "out-of-scope" in str(exc)
    else:
        raise AssertionError("expected out-of-scope tool to stop")
    assert len(client.calls) == 1
    assert len(budget) == 1


def test_synthetic_smoke_is_separate_and_uses_exactly_two_calls():
    first = {"stopReason": "tool_use", "output": {"message": {"role": "assistant", "content": [{
        "toolUse": {"toolUseId": "smoke-1", "name": investigate.SMOKE_TOOL_NAME, "input": {"record": "smoke"}}
    }] }}, "usage": {"inputTokens": 4, "outputTokens": 2}}
    final = {"stopReason": "end_turn", "output": {"message": {"content": [{"text": "Synthetic smoke fixture: status 200, durationMs 1.23."}]}}, "usage": {"inputTokens": 8, "outputTokens": 5}}
    client = FakeConverse([first, final])
    budget = []
    result = investigate.run_tool_smoke(client, call_budget=lambda *args, **kwargs: budget.append((args, kwargs)))
    assert len(client.calls) == 2
    assert budget == [(('smoke',), {'start_smoke_run': True}), (('smoke',), {})]
    assert result["test_only"] is True
    assert result["tool_result"]["app_log"]["requestId"] == "SYNTHETIC-SMOKE-ONLY"
    assert all(call["messages"][0]["content"][0]["text"].find("synthetic") >= 0 for call in client.calls)
    assert client.calls[1]["toolConfig"]["tools"][0]["toolSpec"]["name"] == investigate.SMOKE_TOOL_NAME
    assert "toolChoice" not in client.calls[1]["toolConfig"]


def test_synthetic_smoke_persists_first_response_usage_if_final_call_fails(tmp_path):
    first = {"stopReason": "tool_use", "output": {"message": {"content": [{
        "toolUse": {"toolUseId": "smoke-1", "name": investigate.SMOKE_TOOL_NAME, "input": {"record": "smoke"}}
    }]}}, "usage": {"inputTokens": 4, "outputTokens": 2}}
    client = FakeConverse([first, RuntimeError("toolConfig validation failed")])
    output_path = tmp_path / "smoke-test-only.json"
    budget = []
    try:
        investigate.run_tool_smoke(client, call_budget=lambda *args, **kwargs: budget.append((args, kwargs)), output_path=output_path)
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected the final Converse error to propagate")
    saved = json.loads(output_path.read_text(encoding="utf-8"))
    assert len(client.calls) == 2
    assert budget == [(('smoke',), {'start_smoke_run': True}), (('smoke',), {})]
    assert saved["status"] == "failed"
    assert saved["first_response_usage"] == first["usage"]
    assert saved["api_timing_ms"]["first_request_and_tool_ms"] >= 0
    assert saved["api_timing_ms"]["final_request_ms"] >= 0
    assert saved["error"]["message"] == "toolConfig validation failed"
    assert "toolConfig" not in saved["final_response_usage"]


def test_synthetic_smoke_stops_without_retry_on_wrong_tool():
    first = {"stopReason": "tool_use", "output": {"message": {"content": [{
        "toolUse": {"toolUseId": "smoke-1", "name": "search_run_logs", "input": {"record": "smoke"}}
    }]}}}
    client = FakeConverse([first])
    budget = []
    try:
        investigate.run_tool_smoke(client, call_budget=lambda *args, **kwargs: budget.append((args, kwargs)))
    except ValueError as exc:
        assert "out-of-scope" in str(exc)
    else:
        raise AssertionError("expected out-of-scope tool to stop")
    assert len(client.calls) == 1
    assert len(budget) == 1


def test_investigation_persists_first_response_usage_if_final_call_fails(tmp_path):
    first = {"stopReason": "tool_use", "output": {"message": {"content": [{
        "toolUse": {"toolUseId": "tool-1", "name": investigate.TOOL_NAME, "input": {"selection": "only"}}
    }]}}, "usage": {"inputTokens": 10, "outputTokens": 5}}
    client = FakeConverse([first, RuntimeError("final request failed")])
    output_path = tmp_path / "ai-investigation-req-1.json"
    human = {"request_id": "req-1", "query": "Question", "findings": "findings", "evidence_request_ids": ["req-1"], "uncertainty": "", "started_at": "", "completed_at": ""}
    evidence = {"success": {"event": "request_complete", "requestId": "req-1", "status": 200, "durationMs": 12}}
    try:
        investigate.run_investigation(client, human, evidence, call_budget=lambda *args, **kwargs: None, output_path=output_path)
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected the final Converse error to propagate")
    saved = json.loads(output_path.read_text(encoding="utf-8"))
    assert len(client.calls) == 2
    assert saved["status"] == "failed"
    assert saved["first_response_usage"] == first["usage"]
    assert saved["tool_result"]["app_log"]["requestId"] == "req-1"
    assert saved["api_timing_ms"]["final_request_ms"] >= 0
    assert saved["error"]["message"] == "final request failed"


@pytest.mark.parametrize("workflow", ["main", "smoke"])
@pytest.mark.parametrize(
    ("stop_reason", "content", "partial_text"),
    [
        ("tool_use", [{"toolUse": {"toolUseId": "unexpected", "name": "another_tool", "input": {}}}], ""),
        ("max_tokens", [{"text": "A truncated partial answer."}], "A truncated partial answer."),
        ("guardrail_intervened", [{"text": "A partial guarded response."}], "A partial guarded response."),
        ("end_turn", [], ""),
    ],
)
def test_final_response_is_completed_only_with_end_turn_and_nonempty_text(
        tmp_path, workflow, stop_reason, content, partial_text):
    if workflow == "main":
        first = {"stopReason": "tool_use", "output": {"message": {"content": [{
            "toolUse": {"toolUseId": "tool-1", "name": investigate.TOOL_NAME, "input": {"selection": "only"}}
        }] }}, "usage": {"inputTokens": 10, "outputTokens": 5}}
        human = {"request_id": "req-1", "query": "Question", "findings": "findings", "evidence_request_ids": ["req-1"], "uncertainty": "", "started_at": "", "completed_at": ""}
        evidence = {"success": {"event": "request_complete", "requestId": "req-1", "status": 200, "durationMs": 12}}
    else:
        first = {"stopReason": "tool_use", "output": {"message": {"content": [{
            "toolUse": {"toolUseId": "smoke-1", "name": investigate.SMOKE_TOOL_NAME, "input": {"record": "smoke"}}
        }] }}, "usage": {"inputTokens": 4, "outputTokens": 2}}
        human = None
        evidence = None
    final = {"stopReason": stop_reason, "output": {"message": {"content": content}},
             "usage": {"inputTokens": 12, "outputTokens": 6}}
    client = FakeConverse([first, final])
    budget = []
    output_path = tmp_path / f"{workflow}-incomplete.json"
    if workflow == "main":
        def run():
            return investigate.run_investigation(
                client, human, evidence,
                call_budget=lambda *args, **kwargs: budget.append((args, kwargs)),
                output_path=output_path,
            )
    else:
        def run():
            return investigate.run_tool_smoke(
                client,
                call_budget=lambda *args, **kwargs: budget.append((args, kwargs)),
                output_path=output_path,
            )
    with pytest.raises(ValueError):
        run()

    saved = json.loads(output_path.read_text(encoding="utf-8"))
    assert len(client.calls) == 2
    assert len(budget) == 2
    assert saved["status"] == "failed"
    assert saved["final_response_stop_reason"] == stop_reason
    assert saved["final_response_content"] == content
    assert saved["partial_response_text"] == partial_text
    assert saved["final_response_usage"] == final["usage"]
    assert saved["api_timing_ms"]["final_request_ms"] >= 0


def test_call_budget_reserves_eight_main_and_one_two_call_smoke(tmp_path, monkeypatch):
    usage_path = tmp_path / "api-usage.json"
    monkeypatch.setattr(investigate, "USAGE_PATH", usage_path)

    for index in range(investigate.MAX_MAIN_INVESTIGATIONS):
        request_id = f"req-{index}"
        investigate._consume_call_budget("main", request_id=request_id, start_investigation=True)
        investigate._consume_call_budget("main", request_id=request_id)

    usage = json.loads(usage_path.read_text(encoding="utf-8"))
    assert usage["main_calls"] == 8
    assert len(usage["main_request_calls"]) == 4
    try:
        investigate._consume_call_budget("main", request_id="req-extra", start_investigation=True)
    except ValueError as exc:
        assert "budget is exhausted" in str(exc)
    else:
        raise AssertionError("expected the main budget to stop after four investigations")

    investigate._consume_call_budget("smoke", start_smoke_run=True)
    investigate._consume_call_budget("smoke")
    usage = json.loads(usage_path.read_text(encoding="utf-8"))
    assert usage["smoke_calls"] == 2
    assert usage["smoke_runs"] == 1
    try:
        investigate._consume_call_budget("smoke", start_smoke_run=True)
    except ValueError as exc:
        assert "one-run synthetic smoke budget" in str(exc)
    else:
        raise AssertionError("expected repeated synthetic smoke to be rejected")


def test_call_budget_refuses_legacy_usage_without_resetting_it(tmp_path, monkeypatch):
    usage_path = tmp_path / "api-usage.json"
    usage_path.write_text('{"calls": 3}\n', encoding="utf-8")
    monkeypatch.setattr(investigate, "USAGE_PATH", usage_path)
    try:
        investigate._consume_call_budget("smoke", start_smoke_run=True)
    except ValueError as exc:
        assert "invalid" in str(exc)
    else:
        raise AssertionError("expected an unknown/legacy counter to stop without reset")
    assert usage_path.read_text(encoding="utf-8") == '{"calls": 3}\n'
