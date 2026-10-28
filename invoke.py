#!/usr/bin/env python3
"""Invoke only an HTTPS Lambda Function URL in the selected AWS Region."""
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


if len(sys.argv) != 3:
    raise SystemExit("usage: python3 invoke.py FUNCTION_URL '{\"sku\":\"SKU-RED-01\",\"quantity\":2}'")
url, payload = sys.argv[1], sys.argv[2]
parsed = urllib.parse.urlsplit(url)
try:
    port = parsed.port
except ValueError:
    raise SystemExit("URL contains an invalid port")
if parsed.scheme != "https" or parsed.username or parsed.password or port or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
    raise SystemExit("URL must be a plain HTTPS Lambda Function URL without user info, port, path, query, or fragment")
match = re.fullmatch(r"[a-z0-9-]+\.lambda-url\.([a-z0-9-]+)\.on\.aws", parsed.hostname or "")
if not match:
    raise SystemExit("URL hostname is not an AWS Lambda Function URL")
url_region = match.group(1)
session = boto3.Session()
region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or session.region_name
if not region or url_region != region:
    raise SystemExit(f"URL Region ({url_region}) must match the active AWS Region ({region or 'unset'})")
stack_name = os.environ.get("STACK_NAME")
if not stack_name:
    raise SystemExit("Set STACK_NAME to the deployed sample stack")
stack = session.client("cloudformation", region_name=region).describe_stacks(StackName=stack_name)["Stacks"][0]
owned = any(tag["Key"] == "SampleOwner" and tag["Value"] == "aws-observability-sample-app" for tag in stack.get("Tags", []))
expected_url = next((item["OutputValue"] for item in stack.get("Outputs", []) if item["OutputKey"] == "FunctionUrl"), None)
if not owned or expected_url != url:
    raise SystemExit("URL does not match the owned sample stack named by STACK_NAME")
credentials = session.get_credentials()
if credentials is None:
    raise SystemExit("No active AWS credentials are available")
request = AWSRequest(method="POST", url=url, data=payload.encode(), headers={"content-type": "application/json"})
SigV4Auth(credentials.get_frozen_credentials(), "lambda", region).add_auth(request)
req = urllib.request.Request(url, data=payload.encode(), headers=dict(request.headers), method="POST")
opener = urllib.request.build_opener(NoRedirect)
try:
    with opener.open(req, timeout=15) as response:
        print(f"HTTP {response.status}")
        print(response.read().decode())
except urllib.error.HTTPError as exc:
    print(f"HTTP {exc.code}")
    print(exc.read().decode())
