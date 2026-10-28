#!/usr/bin/env bash
set -euo pipefail
: "${STACK_NAME:?Set STACK_NAME to a unique stack name before deployment}"
[[ "$STACK_NAME" =~ ^[a-zA-Z][a-zA-Z0-9-]{2,30}$ ]] || { echo "Invalid STACK_NAME" >&2; exit 2; }
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-ap-northeast-1}}"
OWNER="aws-observability-sample-app"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STACK_INFO="$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" --output json 2>&1)" || {
  [[ "$STACK_INFO" == *"does not exist"* ]] || { echo "$STACK_INFO" >&2; exit 1; }
  STACK_INFO=""
}
if [[ -n "$STACK_INFO" ]]; then
  python3 -c 'import json,sys; s=json.load(sys.stdin)["Stacks"][0]; sys.exit(0 if any(t["Key"]=="SampleOwner" and t["Value"]=="aws-observability-sample-app" for t in s.get("Tags",[])) else 1)' <<<"$STACK_INFO" || { echo "Existing stack is not owned by this sample; refusing update" >&2; exit 1; }
fi
RENDERED="$(mktemp --suffix=.json)"
trap 'rm -f "$RENDERED"' EXIT
python3 "$HERE/render_template.py" "$RENDERED"
aws cloudformation deploy --template-file "$RENDERED" --stack-name "$STACK_NAME" --region "$REGION" --capabilities CAPABILITY_IAM --tags "SampleOwner=$OWNER" --parameter-overrides "StackPrefix=$STACK_NAME"
aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" --query "Stacks[0].Outputs" --output table
