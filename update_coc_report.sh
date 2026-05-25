#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

TARGET_BRANCH="${TARGET_BRANCH:-main}"
REMOTE_NAME="${REMOTE_NAME:-origin}"
REMOTE_REF="${REMOTE_NAME}/${TARGET_BRANCH}"

sync_with_origin() {
  echo "Fetching latest ${REMOTE_REF}..."
  git fetch "${REMOTE_NAME}"

  local local_head
  local remote_head
  local merge_base

  local_head="$(git rev-parse HEAD)"
  remote_head="$(git rev-parse "${REMOTE_REF}")"
  merge_base="$(git merge-base HEAD "${REMOTE_REF}")"

  if [[ "${local_head}" == "${remote_head}" ]]; then
    echo "Local branch is up to date with ${REMOTE_REF}."
    return
  fi

  if [[ "${local_head}" == "${merge_base}" ]]; then
    if [[ -n "$(git status --porcelain)" ]]; then
      echo "ERROR: Local branch is behind ${REMOTE_REF}, but the working tree is dirty." >&2
      echo "Refusing to pull over local changes. Resolve the working tree and rerun." >&2
      exit 1
    fi

    # The updater runs unattended from systemd, so only a clean fast-forward is
    # safe. A blind rebase or merge could rewrite/reconcile history incorrectly
    # while site_output commits are being generated on a timer.
    echo "Local branch is behind ${REMOTE_REF}; fast-forwarding only..."
    git merge --ff-only "${REMOTE_REF}"
    return
  fi

  if [[ "${remote_head}" == "${merge_base}" ]]; then
    if [[ -n "$(git status --porcelain)" ]]; then
      echo "ERROR: Local branch is ahead of ${REMOTE_REF}, but the working tree is dirty." >&2
      echo "Refusing to push while local changes are present. Resolve the working tree and rerun." >&2
      exit 1
    fi

    echo "Local branch ahead; pushing existing commits before build..."
    git push "${REMOTE_NAME}" "HEAD:${TARGET_BRANCH}"
    return
  fi

  echo "ERROR: Local branch has diverged from ${REMOTE_REF}." >&2
  echo "Refusing to auto-rebase or merge in the updater." >&2
  echo "Fast-forward-only is safer here because the timer runs unattended and should not rewrite or reconcile history." >&2
  exit 1
}

if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi

export COC_API_TOKEN="${COC_API_TOKEN:-${CLASH_API_TOKEN:-}}"
export COC_CLAN_TAG="${COC_CLAN_TAG:-${CLAN_TAG:-}}"

sync_with_origin

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
