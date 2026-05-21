#!/usr/bin/env bash

set -euo pipefail

echo "Building CoC report site..."
if ! python3 fetch_current_war_snapshot.py; then
  echo "Current war snapshot refresh failed; building from the latest saved snapshot if available."
fi

python3 build_site.py --include-current-war

echo "Checking for site changes..."
if [[ -z "$(git status --porcelain site_output/)" ]]; then
  echo "No site changes to deploy."
  exit 0
fi

echo "Committing updated site..."
git add site_output/
git commit -m "Update CoC report site"

echo "Pushing to GitHub..."
git push

echo "Deploy complete. Cloudflare Pages will redeploy automatically."
