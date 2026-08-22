#!/usr/bin/env bash
# Build, deploy, and publish the site in one shot.
set -euo pipefail

STACK="${STACK:-commit-muse}"
REGION="${AWS_REGION:-us-east-1}"
GITHUB_USER="${GITHUB_USER:-midnight480}"
cd "$(dirname "$0")/.."

sam build
sam deploy \
  --stack-name "$STACK" \
  --region "$REGION" \
  --resolve-s3 \
  --capabilities CAPABILITY_IAM \
  --no-confirm-changeset \
  --no-fail-on-empty-changeset \
  --parameter-overrides "GitHubUser=$GITHUB_USER"

exec "$(dirname "$0")/publish-web.sh"
