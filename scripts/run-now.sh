#!/usr/bin/env bash
# Wake the agent up by hand (it normally does this on its own at 22:00 JST).
set -euo pipefail
STACK="${STACK:-commit-muse}"
REGION="${AWS_REGION:-us-east-1}"
PAYLOAD="${1:-}"
[ -z "$PAYLOAD" ] && PAYLOAD='{}'
PAYLOAD_FILE="$(mktemp)"
printf '%s' "$PAYLOAD" > "$PAYLOAD_FILE"
FN="$(aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='MuseFunctionName'].OutputValue" --output text)"
TMP="$(mktemp)"
LOG="$(aws lambda invoke --function-name "$FN" --region "$REGION" \
  --cli-binary-format raw-in-base64-out --payload "file://$PAYLOAD_FILE" \
  --log-type Tail --query 'LogResult' --output text "$TMP")"
echo "--- last log lines ---"
echo "$LOG" | base64 --decode | tail -20
echo "--- response ---"
cat "$TMP"; echo
rm -f "$TMP" "$PAYLOAD_FILE"
