#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi

export COC_API_TOKEN="${COC_API_TOKEN:-${CLASH_API_TOKEN:-}}"
export COC_CLAN_TAG="${COC_CLAN_TAG:-${CLAN_TAG:-}}"

echo "Building CoC report site..."
python3 build_site.py --include-current-war --live-current-war-fallback

echo "Checking for site changes..."
git add -- site_output

if git diff --cached --quiet -- site_output; then
  echo "No site changes to deploy."
  exit 0
fi

echo "Committing updated site..."
git commit -m "Update clan report site" -- site_output

echo "Pushing to GitHub..."
git push

echo "Deploy complete. Cloudflare Pages will redeploy automatically."
