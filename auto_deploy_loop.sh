#!/usr/bin/env bash

set -u

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$*"
}

stop_loop() {
  log "Auto deploy loop stopped."
  exit 0
}

deploy_once() {
  log "Building CoC report site..."
  if ! python3 build_site.py --include-current-war; then
    log "Build failed; will retry after the next sleep interval."
    return
  fi

  log "Checking for site changes..."
  if [[ -z "$(git status --porcelain site_output/)" ]]; then
    log "No site changes to deploy."
    return
  fi

  log "Committing updated site output..."
  if ! git add site_output/; then
    log "git add failed; will retry later."
    return
  fi

  if git diff --cached --quiet -- site_output/; then
    log "No staged site changes to deploy."
    return
  fi

  if ! git commit -m "Update CoC report site"; then
    log "git commit failed; will retry later."
    return
  fi

  log "Pushing to GitHub..."
  if ! git push; then
    log "git push failed; local commit remains for the next manual check."
    return
  fi

  log "Deploy complete. Cloudflare Pages will redeploy automatically."
}

next_sleep_seconds() {
  local seconds
  if ! seconds="$(python3 war_poll_interval.py)"; then
    echo "3600"
    return
  fi

  if [[ "$seconds" =~ ^[0-9]+$ ]]; then
    echo "$seconds"
  else
    echo "3600"
  fi
}

trap stop_loop INT TERM

log "Starting CoC report auto deploy loop."

while true; do
  deploy_once

  sleep_seconds="$(next_sleep_seconds)"
  log "Sleeping for ${sleep_seconds} seconds."
  sleep "$sleep_seconds" || stop_loop
done
