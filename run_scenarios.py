"""Run four real requests and print each HTTP response with its matching app log."""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


url = sys.argv[1] if len(sys.argv) == 2 else ""
parsed = urllib.parse.urlsplit(url)
match = re.fullmatch(r"[a-z0-9-]+\.lambda-url\.([a-z0-9-]+)\.on\.aws", parsed.hostname or "")
if parsed.scheme != "https" or not match or parsed.username or parsed.password or parsed.port or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
    raise SystemExit("Pass a plain HTTPS Lambda Function URL")
session = boto3.Session()
region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or session.region_name
if not region or region != match.group(1):
    raise SystemExit("Function URL Region must match the active AWS Region")
stack_name = os.environ.get("STACK_NAME")
if not stack_name:
    raise SystemExit("Set STACK_NAME to the deployed sample stack")
log_group = f"/aws/lambda/{stack_name}"
stack = session.client("cloudformation", region_name=region).describe_stacks(StackName=stack_name)["Stacks"][0]
owned = any(tag["Key"] == "SampleOwner" and tag["Value"] == "aws-observability-sample-app" for tag in stack.get("Tags", []))
expected_url = next((item["OutputValue"] for item in stack.get("Outputs", []) if item["OutputKey"] == "FunctionUrl"), None)
if not owned or expected_url != url:
    raise SystemExit("URL does not match the owned sample stack named by STACK_NAME")
scenarios = [
    ("success", {"sku": "SKU-RED-01", "quantity": 2}),
    ("validation", {"sku": "SKU-RED-01", "quantity": 0}),
    ("injected_failure", {"mode": "failure", "sku": "SKU-RED-01", "quantity": 1}),
    ("bounded_delay", {"mode": "delay", "delayMs": 1500, "sku": "SKU-BLUE-02", "quantity": 2}),
]
opener = urllib.request.build_opener(NoRedirect)
logs = session.client("logs", region_name=region)
credentials = session.get_credentials()
if credentials is None:
    raise SystemExit("No active AWS credentials are available")
for label, body in scenarios:
    payload = json.dumps(body).encode()
    request = AWSRequest(method="POST", url=url, data=payload, headers={"content-type": "application/json"})
    SigV4Auth(credentials.get_frozen_credentials(), "lambda", region).add_auth(request)
    req = urllib.request.Request(url, data=payload, headers=dict(request.headers), method="POST")
    try:
        with opener.open(req, timeout=15) as response:
            status, result = response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        status, result = exc.code, json.loads(exc.read() or b"{}")
    request_id = result.get("requestId")
    print(f"\n=== {label}: HTTP {status} ===")
    print("Request input:")
    print(json.dumps(body, ensure_ascii=False))
    print("HTTP response:")
    print(json.dumps(result, ensure_ascii=False))
    if not request_id:
        raise SystemExit("Response had no requestId; stop and investigate before continuing")
    app_logs = []
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline and not app_logs:
        token = None
        seen_tokens = set()
        for _ in range(20):
            args = {"logGroupName": log_group, "filterPattern": f'"{request_id}"', "limit": 100}
            if token:
                args["nextToken"] = token
            page = logs.filter_log_events(**args)
            for event in page.get("events", []):
                try:
                    value = json.loads(event["message"])
                except json.JSONDecodeError:
                    continue
                if value.get("event") == "request_complete" and value.get("requestId") == request_id:
                    app_logs.append(value)
            token = page.get("nextToken")
            if not token or token in seen_tokens or app_logs:
                break
            seen_tokens.add(token)
        if not app_logs:
            time.sleep(2)
    if len(app_logs) != 1:
        raise SystemExit(f"Expected one app JSON log for {request_id}; found {len(app_logs)}. Check Lambda START/END/REPORT separately.")
    print("Matching CloudWatch app JSON log:")
    print(json.dumps(app_logs[0], ensure_ascii=False))
