#!/usr/bin/env bash
set -euo pipefail
: "${STACK_NAME:?Set STACK_NAME to the exact stack name before cleanup}"
[[ "$STACK_NAME" =~ ^[a-zA-Z][a-zA-Z0-9-]{2,30}$ ]] || { echo "Invalid STACK_NAME" >&2; exit 2; }
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-ap-northeast-1}}"
OWNER="aws-observability-sample-app"
STACK_INFO="$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" --output json 2>&1)" || { echo "$STACK_INFO" >&2; exit 1; }
python3 -c 'import json,sys; s=json.load(sys.stdin)["Stacks"][0]; sys.exit(0 if any(t["Key"]=="SampleOwner" and t["Value"]=="aws-observability-sample-app" for t in s.get("Tags",[])) else 1)' <<<"$STACK_INFO" || { echo "Stack ownership tag missing or mismatched; refusing deletion" >&2; exit 1; }
OUTPUTS="$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" --query "Stacks[0].Outputs" --output json)"
FUNCTION="$(python3 -c 'import json,sys; print(next(x["OutputValue"] for x in json.load(sys.stdin) if x["OutputKey"]=="FunctionName"))' <<<"$OUTPUTS")"
LOG_GROUP="$(python3 -c 'import json,sys; print(next(x["OutputValue"] for x in json.load(sys.stdin) if x["OutputKey"]=="LogGroupName"))' <<<"$OUTPUTS")"
ROLE="$(python3 -c 'import json,sys; print(next(x["OutputValue"] for x in json.load(sys.stdin) if x["OutputKey"]=="RoleName"))' <<<"$OUTPUTS")"
aws cloudformation delete-stack --stack-name "$STACK_NAME" --region "$REGION"
aws cloudformation wait stack-delete-complete --stack-name "$STACK_NAME" --region "$REGION"
STACK_CHECK="$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" 2>&1)" && { echo "Stack still exists: $STACK_NAME" >&2; exit 1; }
[[ "$STACK_CHECK" == *"does not exist"* ]] || { echo "$STACK_CHECK" >&2; exit 1; }
check_missing() {
  local label="$1"; shift
  local result
  result="$("$@" 2>&1)" && { echo "$label still exists" >&2; exit 1; }
  [[ "$result" == *"ResourceNotFoundException"* || "$result" == *"NoSuchEntity"* ]] || { echo "$result" >&2; exit 1; }
}
check_missing "Lambda function" aws lambda get-function --function-name "$FUNCTION" --region "$REGION"
check_missing "Function URL" aws lambda get-function-url-config --function-name "$FUNCTION" --region "$REGION"
check_missing "IAM role" aws iam get-role --role-name "$ROLE"
LOG_GROUPS_JSON="$(aws logs describe-log-groups --log-group-name-prefix "$LOG_GROUP" --region "$REGION" --output json)"
python3 -c 'import json,sys; n=sys.argv[1]; sys.exit(1 if any(x["logGroupName"]==n for x in json.load(sys.stdin)["logGroups"]) else 0)' "$LOG_GROUP" <<<"$LOG_GROUPS_JSON" || { echo "CloudWatch log group still exists: $LOG_GROUP" >&2; exit 1; }
echo "Deleted stack and confirmed exact Lambda function, Function URL, IAM role, and log group are absent."
