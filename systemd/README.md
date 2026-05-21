# systemd Templates

Current Droplet state from the latest operator audit:

```text
clashcommand.service       active Discord bot
coc-war-snapshot.service  active final war snapshot watcher
coc-report-updater.timer  active 15-minute site updater
coc-report-deploy.timer   disabled duplicate updater
```

This directory currently contains:

```text
coc-war-snapshot.service
coc-report-updater.service
coc-report-updater.timer
coc-report-deploy.service
coc-report-deploy.timer
```

Use `coc-war-snapshot.service` for the final war watcher.

Use `coc-report-updater.service` and `coc-report-updater.timer` for the active static site updater. The timer runs every 15 minutes and executes:

```text
/opt/clashcommand/app/update_coc_report.sh
```

Treat `coc-report-deploy.*` as inactive duplicate deploy automation unless intentionally replacing `coc-report-updater.timer`. Do not run both updater timers at the same time.
