#!/usr/bin/env bash
# Push web/ to the private bucket and point the page at the feedback API.
set -euo pipefail

STACK="${STACK:-commit-muse}"
REGION="${AWS_REGION:-us-east-1}"
cd "$(dirname "$0")/.."

out() {
  aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

BUCKET="$(out SiteBucketName)"
ENDPOINT="$(out FeedbackEndpoint)"
SITE="$(out SiteUrl)"
DIST="$(out DistributionId)"

printf '{"feedbackEndpoint":"%s"}' "$ENDPOINT" > web/config.json

aws s3 cp web/index.html "s3://$BUCKET/index.html" \
  --content-type "text/html; charset=utf-8" --cache-control "public, max-age=300" --region "$REGION"
aws s3 cp web/config.json "s3://$BUCKET/config.json" \
  --content-type "application/json" --cache-control "public, max-age=300" --region "$REGION"

aws cloudfront create-invalidation --distribution-id "$DIST" --paths "/index.html" "/config.json" \
  --query "Invalidation.Id" --output text >/dev/null

echo "site      : $SITE"
echo "feedback  : $ENDPOINT"
