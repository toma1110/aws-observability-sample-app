"""One-run, one-tool, read-only log investigation using Bedrock Converse."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import time

import boto3
from botocore.config import Config

MODEL_ID = "amazon.nova-lite-v1:0"
SCENARIOS = {"success", "validation", "injected_failure", "bounded_delay"}
MAX_CALLS_PER_INVESTIGATION = 2
MAX_MAIN_INVESTIGATIONS = 4
MAX_MAIN_CALLS = MAX_MAIN_INVESTIGATIONS * MAX_CALLS_PER_INVESTIGATION
MAX_SMOKE_RUNS = 1
MAX_SMOKE_CALLS = 2
TOTAL_CALL_BUDGET = MAX_MAIN_CALLS + MAX_SMOKE_CALLS
MAX_OUTPUT_TOKENS = 350
MAX_EVIDENCE_BYTES = 64 * 1024
TOOL_NAME = "search_run_logs"
SMOKE_TOOL_NAME = "search_synthetic_log"

CONSUMER_DIR = Path(__file__).resolve().parent
REPOSITORY_DIR = CONSUMER_DIR.parents[2]
HUMAN_RECORD_PATH = CONSUMER_DIR / "human-investigation.json"
EVIDENCE_PATH = REPOSITORY_DIR / "run-evidence.txt"
USAGE_PATH = CONSUMER_DIR / "api-usage.json"

FORCED_TOOL_CONFIG = {
    "toolChoice": {"tool": {"name": TOOL_NAME}},
    "tools": [{"toolSpec": {
        "name": TOOL_NAME,
        "description": "Return only the one completed app JSON log selected in the human source record from the current run-evidence.txt. This tool cannot open files or access AWS.",
        "inputSchema": {"json": {
            "type": "object",
            "properties": {"selection": {"type": "string", "enum": ["only"]}},
            "required": ["selection"],
            "additionalProperties": False,
        }},
    }}],
}
FINAL_TOOL_CONFIG = {"tools": FORCED_TOOL_CONFIG["tools"]}


def parse_run_evidence(raw: str) -> dict[str, dict]:
    """Extract only the explicitly labelled app JSON log lines from README output."""
    result: dict[str, dict] = {}
    current = None
    want_log = False
    for line in raw.splitlines():
        header = re.fullmatch(r"=== (success|validation|injected_failure|bounded_delay): HTTP \d{3} ===", line.strip())
        if header:
            current = header.group(1)
            want_log = False
            continue
        if line.strip() == "Matching CloudWatch app JSON log:":
            want_log = True
            continue
        if want_log and current:
            want_log = False
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (isinstance(entry, dict) and entry.get("event") == "request_complete"
                    and isinstance(entry.get("requestId"), str) and entry["requestId"]
                    and isinstance(entry.get("status"), int)
                    and isinstance(entry.get("durationMs"), (int, float))):
                if current in result:
                    raise ValueError(f"More than one matching completion log for {current}; use one run only")
                result[current] = entry
    return result


def load_evidence(path: Path = EVIDENCE_PATH) -> dict[str, dict]:
    resolved = path.resolve(strict=True)
    if resolved != EVIDENCE_PATH.resolve():
        raise ValueError("Evidence must be the current repository's run-evidence.txt")
    raw = resolved.read_bytes()
    if len(raw) > MAX_EVIDENCE_BYTES:
        raise ValueError("run-evidence.txt exceeds the 64 KiB limit; stop and inspect the run")
    parsed = parse_run_evidence(raw.decode("utf-8"))
    if not parsed:
        raise ValueError("No recognized completed app JSON logs were found")
    return parsed


def _timestamp(value, field):
    if not isinstance(value, str) or not value:
        raise ValueError(f"Human record needs {field} with its actual time and timezone")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"Human record {field} must include a timezone")
    return parsed


def load_human_record(path: Path = HUMAN_RECORD_PATH) -> tuple[dict, int]:
    resolved = path.resolve(strict=True)
    if resolved != HUMAN_RECORD_PATH.resolve():
        raise ValueError("Human record must be consumer-local human-investigation.json")
    human = json.loads(resolved.read_text(encoding="utf-8"))
    required = {"request_id", "query", "started_at", "completed_at", "findings", "evidence_request_ids", "uncertainty"}
    if not isinstance(human, dict) or set(human) != required:
        raise ValueError("Copy human-investigation.template.json and fill each field")
    if not isinstance(human["request_id"], str) or not human["request_id"].strip() or not isinstance(human["query"], str) or not human["query"].strip():
        raise ValueError("Human record needs one actual requestId and the exact question you investigated")
    if not isinstance(human["findings"], str) or not human["findings"].strip() or not isinstance(human["uncertainty"], str):
        raise ValueError("Record your actual findings and any uncertainty before AI comparison")
    if not isinstance(human["evidence_request_ids"], list) or not human["evidence_request_ids"]:
        raise ValueError("Record the requestId(s) you actually checked before AI comparison")
    start = _timestamp(human["started_at"], "started_at")
    end = _timestamp(human["completed_at"], "completed_at")
    duration_ms = int((end - start).total_seconds() * 1000)
    if duration_ms <= 0 or duration_ms > 12 * 60 * 60 * 1000:
        raise ValueError("Recorded investigation times must be ordered and within 12 hours")
    entries = load_evidence()
    matching = [entry for entry in entries.values() if entry["requestId"] == human["request_id"]]
    if len(matching) != 1:
        raise ValueError("The selected requestId must match exactly one log in the current run-evidence.txt")
    if human["request_id"] not in human["evidence_request_ids"]:
        raise ValueError("Human evidence requestId does not match the selected actual run log")
    return human, duration_ms


def find_log_by_request_id(request_id: str, evidence: dict[str, dict]) -> dict:
    matches = [entry for entry in evidence.values() if entry["requestId"] == request_id]
    if len(matches) != 1:
        raise ValueError("Selected requestId must match exactly one log in the current run")
    return matches[0]


def search_run_logs(request_id: str, evidence: dict[str, dict]) -> dict:
    """Return only one actual app JSON record; keep its case label outside model context."""
    return {"app_log": find_log_by_request_id(request_id, evidence)}


def _load_call_usage() -> dict:
    usage = {
        "main_calls": 0,
        "main_request_calls": {},
        "smoke_calls": 0,
        "smoke_runs": 0,
    }
    if USAGE_PATH.exists():
        usage = json.loads(USAGE_PATH.read_text(encoding="utf-8"))
    required = {"main_calls", "main_request_calls", "smoke_calls", "smoke_runs"}
    if not isinstance(usage, dict) or set(usage) != required:
        raise ValueError("api-usage.json is invalid; stop and inspect it")
    for key in ("main_calls", "smoke_calls", "smoke_runs"):
        if type(usage[key]) is not int or usage[key] < 0:
            raise ValueError("api-usage.json is invalid; stop and inspect it")
    if not isinstance(usage["main_request_calls"], dict):
        raise ValueError("api-usage.json is invalid; stop and inspect it")
    if any(type(value) is not int or value < 0 or value > MAX_CALLS_PER_INVESTIGATION
           for value in usage["main_request_calls"].values()):
        raise ValueError("api-usage.json is invalid; stop and inspect it")
    if (usage["main_calls"] > MAX_MAIN_CALLS
            or len(usage["main_request_calls"]) > MAX_MAIN_INVESTIGATIONS
            or usage["smoke_calls"] > MAX_SMOKE_CALLS
            or usage["smoke_runs"] > MAX_SMOKE_RUNS
            or any(value == 0 for value in usage["main_request_calls"].values())):
        raise ValueError("api-usage.json exceeds its run limits; stop and inspect it")
    if sum(usage["main_request_calls"].values()) != usage["main_calls"]:
        raise ValueError("api-usage.json call counts disagree; stop and inspect it")
    return usage


def _consume_call_budget(kind: str, request_id: str | None = None,
                         start_investigation: bool = False,
                         start_smoke_run: bool = False):
    usage = _load_call_usage()
    if kind == "main":
        if start_smoke_run or not isinstance(request_id, str) or not request_id:
            raise ValueError("Main call budget needs the selected request ID")
        if start_investigation:
            if request_id in usage["main_request_calls"]:
                raise ValueError("This request ID already used its main investigation; do not retry")
            if len(usage["main_request_calls"]) >= MAX_MAIN_INVESTIGATIONS:
                raise ValueError("The four-investigation main Bedrock budget is exhausted; stop")
            if usage["main_calls"] + MAX_CALLS_PER_INVESTIGATION > MAX_MAIN_CALLS:
                raise ValueError("Not enough reserved main calls remain for a two-call investigation; stop")
            usage["main_request_calls"][request_id] = 0
        elif request_id not in usage["main_request_calls"]:
            raise ValueError("Start a main investigation before its final call")
        if usage["main_request_calls"][request_id] >= MAX_CALLS_PER_INVESTIGATION:
            raise ValueError("This request ID reached its two-call investigation limit; stop")
        usage["main_request_calls"][request_id] += 1
        usage["main_calls"] += 1
    elif kind == "smoke":
        if request_id is not None or start_investigation:
            raise ValueError("Synthetic smoke budget cannot use an app request ID")
        if start_smoke_run:
            if usage["smoke_runs"] >= MAX_SMOKE_RUNS:
                raise ValueError("The one-run synthetic smoke budget is exhausted; stop")
            usage["smoke_runs"] += 1
        elif usage["smoke_runs"] != 1:
            raise ValueError("Start the one synthetic smoke run before its final call")
        if usage["smoke_calls"] >= MAX_SMOKE_CALLS:
            raise ValueError("The two-call synthetic smoke budget is exhausted; stop")
        usage["smoke_calls"] += 1
    else:
        raise ValueError("Unknown Bedrock call budget; stop and inspect the caller")
    if usage["main_calls"] + usage["smoke_calls"] > TOTAL_CALL_BUDGET:
        raise ValueError("This local run exceeded its 10-call Bedrock budget; stop")
    USAGE_PATH.write_text(json.dumps(usage, indent=2) + "\n", encoding="utf-8")


def _write_result(path: Path | None, result: dict):
    if path is not None:
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _record_failure(result: dict, path: Path | None, error: Exception):
    result["status"] = "failed"
    result["error"] = {"type": type(error).__name__, "message": str(error)}
    _write_result(path, result)


def _record_final_response(result: dict, path: Path | None, final: dict,
                           request_started: float) -> str:
    blocks = final.get("output", {}).get("message", {}).get("content", [])
    text = "\n".join(b["text"] for b in blocks if isinstance(b, dict) and "text" in b)
    result["final_response_stop_reason"] = final.get("stopReason")
    result["final_response_content"] = blocks
    result["partial_response_text"] = text
    result["final_response_usage"] = final.get("usage", {})
    result["api_timing_ms"]["final_request_ms"] = round((time.perf_counter() - request_started) * 1000, 1)
    if result["final_response_stop_reason"] != "end_turn":
        _record_failure(
            result, path,
            ValueError(f"Final Converse did not complete (stopReason={result['final_response_stop_reason']}); stop without retry"),
        )
        raise ValueError("Final Converse did not complete; stop without retry")
    if not text.strip():
        _record_failure(result, path, ValueError("Final Converse ended without nonempty text; stop without retry"))
        raise ValueError("Final Converse ended without nonempty text; stop without retry")
    _write_result(path, result)
    return text


def run_investigation(client, human: dict, evidence: dict[str, dict], call_budget=_consume_call_budget,
                      output_path: Path | None = None):
    request_id = human["request_id"]
    before = time.perf_counter()
    call_budget("main", request_id=request_id, start_investigation=True)
    result = {
        "status": "in_progress",
        "model_id": MODEL_ID,
        "request_id": request_id,
        "query": human["query"],
        "api_timing_ms": {},
        "first_response_usage": {},
        "final_response_usage": {},
    }
    try:
        first = client.converse(
            modelId=MODEL_ID,
            system=[{"text": "Investigate only the sample app log returned by the required search tool. Cite exact fields. Separate observed facts from hypotheses and say when this log cannot establish a cause. Never request or infer other files, AWS access, or hidden answer material."}],
            messages=[{"role": "user", "content": [{"text": human["query"]}]}],
            inferenceConfig={"maxTokens": MAX_OUTPUT_TOKENS, "temperature": 0},
            toolConfig=FORCED_TOOL_CONFIG,
        )
    except Exception as exc:
        result["api_timing_ms"]["first_request_and_tool_ms"] = round((time.perf_counter() - before) * 1000, 1)
        _record_failure(result, output_path, exc)
        raise
    result["first_response_usage"] = first.get("usage", {})
    result["api_timing_ms"]["first_request_and_tool_ms"] = round((time.perf_counter() - before) * 1000, 1)
    _write_result(output_path, result)
    blocks = first.get("output", {}).get("message", {}).get("content", [])
    uses = [b["toolUse"] for b in blocks if isinstance(b, dict) and "toolUse" in b]
    if first.get("stopReason") != "tool_use" or len(uses) != 1:
        error = ValueError("Expected exactly one required log search tool call; stop without retry")
        _record_failure(result, output_path, error)
        raise error
    use = uses[0]
    if use.get("name") != TOOL_NAME or use.get("input") != {"selection": "only"}:
        error = ValueError("Model requested an out-of-scope tool/input; stop without retry")
        _record_failure(result, output_path, error)
        raise error
    found = search_run_logs(request_id, evidence)
    after_tool = time.perf_counter()
    call_budget("main", request_id=request_id)
    result.update({
        "human_baseline": {
            "findings": human["findings"],
            "evidence_request_ids": human["evidence_request_ids"],
            "uncertainty": human["uncertainty"],
        },
        "tool_result": found,
    })
    _write_result(output_path, result)
    try:
        final = client.converse(
            modelId=MODEL_ID,
            system=[{"text": "Use only the supplied tool result. Distinguish the log's observed values from possible explanations. Do not claim measured customer impact or time savings."}],
            messages=[
                {"role": "user", "content": [{"text": human["query"]}]},
                first["output"]["message"],
                {"role": "user", "content": [{"toolResult": {
                    "toolUseId": use["toolUseId"],
                    "content": [{"json": found}],
                    "status": "success",
                }}]},
            ],
            inferenceConfig={"maxTokens": MAX_OUTPUT_TOKENS, "temperature": 0},
            toolConfig=FINAL_TOOL_CONFIG,
        )
    except Exception as exc:
        result["api_timing_ms"]["final_request_ms"] = round((time.perf_counter() - after_tool) * 1000, 1)
        _record_failure(result, output_path, exc)
        raise
    text = _record_final_response(result, output_path, final, after_tool)
    result.update({
        "status": "completed",
        "ai_answer": text,
        "api_timing_ms": {
            "first_request_and_tool_ms": round((after_tool - before) * 1000, 1),
            "final_request_ms": round((time.perf_counter() - after_tool) * 1000, 1),
        },
        "note": "Human time is self-recorded in the human-first source; AI review time must be recorded separately by a person.",
    })
    _write_result(output_path, result)
    return result


def run_tool_smoke(client, call_budget=_consume_call_budget, output_path: Path | None = None):
    """Exercise Converse tool-use with a synthetic record, kept outside comparison data."""
    smoke_tool_config = {
        "toolChoice": {"tool": {"name": SMOKE_TOOL_NAME}},
        "tools": [{"toolSpec": {
            "name": SMOKE_TOOL_NAME,
            "description": "Return the one synthetic smoke-test log supplied to this local harness. It is not an app run or comparison evidence.",
            "inputSchema": {"json": {
                "type": "object",
                "properties": {"record": {"type": "string", "enum": ["smoke"]}},
                "required": ["record"],
                "additionalProperties": False,
            }},
        }}],
    }
    synthetic = {"record": "smoke", "app_log": {
        "event": "request_complete", "requestId": "SYNTHETIC-SMOKE-ONLY",
        "status": 200, "durationMs": 1.23, "error": None,
    }}
    prompt = "This is a test-only synthetic log. Use the required tool once, then report only its status and durationMs. Do not present this as an app run or measured result."
    started = time.perf_counter()
    result = {
        "status": "in_progress",
        "test_only": True,
        "model_id": MODEL_ID,
        "tool_result": synthetic,
        "api_timing_ms": {},
        "first_response_usage": {},
        "final_response_usage": {},
        "note": "Synthetic smoke data only. Excluded from the app run and human comparison dataset.",
    }
    call_budget("smoke", start_smoke_run=True)
    try:
        first = client.converse(
            modelId=MODEL_ID,
            system=[{"text": "This is an isolated tool-use smoke test. The synthetic log is not evidence about a real app run."}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": MAX_OUTPUT_TOKENS, "temperature": 0},
            toolConfig=smoke_tool_config,
        )
    except Exception as exc:
        result["api_timing_ms"]["first_request_and_tool_ms"] = round((time.perf_counter() - started) * 1000, 1)
        _record_failure(result, output_path, exc)
        raise
    result["first_response_usage"] = first.get("usage", {})
    result["api_timing_ms"]["first_request_and_tool_ms"] = round((time.perf_counter() - started) * 1000, 1)
    _write_result(output_path, result)
    blocks = first.get("output", {}).get("message", {}).get("content", [])
    uses = [b["toolUse"] for b in blocks if isinstance(b, dict) and "toolUse" in b]
    if first.get("stopReason") != "tool_use" or len(uses) != 1:
        error = ValueError("Smoke expected exactly one required tool call; stop without retry")
        _record_failure(result, output_path, error)
        raise error
    use = uses[0]
    if use.get("name") != SMOKE_TOOL_NAME or use.get("input") != {"record": "smoke"}:
        error = ValueError("Smoke returned an out-of-scope tool/input; stop without retry")
        _record_failure(result, output_path, error)
        raise error
    after_tool = time.perf_counter()
    call_budget("smoke")
    _write_result(output_path, result)
    try:
        final = client.converse(
            modelId=MODEL_ID,
            system=[{"text": "Report the supplied synthetic values only. State that they are a smoke-test fixture, not an app run."}],
            messages=[
                {"role": "user", "content": [{"text": prompt}]},
                first["output"]["message"],
                {"role": "user", "content": [{"toolResult": {
                    "toolUseId": use["toolUseId"], "content": [{"json": synthetic}], "status": "success",
                }}]},
            ],
            inferenceConfig={"maxTokens": MAX_OUTPUT_TOKENS, "temperature": 0},
            toolConfig={"tools": smoke_tool_config["tools"]},
        )
    except Exception as exc:
        result["api_timing_ms"]["final_request_ms"] = round((time.perf_counter() - after_tool) * 1000, 1)
        _record_failure(result, output_path, exc)
        raise
    text = _record_final_response(result, output_path, final, after_tool)
    result.update({
        "status": "completed",
        "ai_answer": text,
        "api_timing_ms": {
            "first_request_and_tool_ms": round((after_tool - started) * 1000, 1),
            "final_request_ms": round((time.perf_counter() - after_tool) * 1000, 1),
        },
    })
    _write_result(output_path, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", default="ap-northeast-1")
    parser.add_argument("--validate-only", action="store_true", help="Check the human record and current app log without calling Bedrock")
    parser.add_argument("--smoke-test-only", action="store_true", help="Make exactly two no-retry Converse calls using a synthetic log; never reads the human record or app evidence")
    parser.add_argument("--show-log", action="store_true", help="Print only one selected app JSON log for the human-first investigation")
    parser.add_argument("--request-id", help="Select the exact app log to display with --show-log")
    args = parser.parse_args()
    if args.region != "ap-northeast-1":
        raise SystemExit("This PoC is fixed to ap-northeast-1; stop if your run requires another Region")
    if sum((args.validate_only, args.smoke_test_only, args.show_log)) > 1:
        raise SystemExit("Choose only one of --validate-only, --smoke-test-only, or --show-log")
    if args.request_id and not args.show_log:
        raise SystemExit("--request-id is only valid with --show-log")
    if args.show_log:
        if not args.request_id:
            raise SystemExit("Choose a requestId with --show-log")
        evidence = load_evidence()
        print(json.dumps({"app_log": find_log_by_request_id(args.request_id, evidence)}, ensure_ascii=False, indent=2))
        return
    if args.smoke_test_only:
        client = boto3.client("bedrock-runtime", region_name=args.region, config=Config(retries={"total_max_attempts": 1}))
        output = CONSUMER_DIR / "smoke-test-only.json"
        result = run_tool_smoke(client, output_path=output)
        _write_result(output, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print(f"Saved test-only result: {output.name}")
        return
    human, _ = load_human_record()
    evidence = load_evidence()
    if args.validate_only:
        print(json.dumps({"requestId": human["request_id"], "bedrock_calls": 0}, indent=2))
        return
    client = boto3.client("bedrock-runtime", region_name=args.region, config=Config(retries={"total_max_attempts": 1}))
    started = time.perf_counter()
    output = CONSUMER_DIR / f"ai-investigation-{human['request_id']}.json"
    result = run_investigation(client, human, evidence, output_path=output)
    result["wall_clock_ms"] = round((time.perf_counter() - started) * 1000, 1)
    _write_result(output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"Saved local result: {output.name}")


if __name__ == "__main__":
    main()
