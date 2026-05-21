#!/usr/bin/env bash

set -euo pipefail

APP_DIR="/opt/clashcommand/app"
ENV_FILE="${APP_DIR}/.env"

cd "${APP_DIR}"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing environment file: ${ENV_FILE}"
  exit 1
fi

set -a
source "${ENV_FILE}"
set +a

export COC_API_TOKEN="${COC_API_TOKEN:-${CLASH_API_TOKEN:-}}"
export COC_CLAN_TAG="${COC_CLAN_TAG:-${CLAN_TAG:-}}"

echo "Building CoC report site..."
python3 build_site.py --include-current-war

echo "Staging generated site output..."
git add -- site_output

if git diff --cached --quiet -- site_output; then
  echo "No generated site changes to deploy."
  exit 0
fi

echo "Committing generated site output..."
git commit -m "Update clan report site" -- site_output

echo "Pushing site update to origin..."
git push origin HEAD

echo "Deploy complete. Cloudflare Pages will redeploy from the pushed commit."
