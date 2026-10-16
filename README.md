# Node.js Watchdog

Third-party [aaPanel](https://www.aapanel.com/) plugin that watches Node.js projects and emails you when one stops unexpectedly — with the last log lines attached so you can see why.

**Version:** 1.0.0  
**Author:** [Tuyen Pham](https://github.com/tuyenlaptrinh)  
**Platform:** Linux + aaPanel

---

## Why

If you host many Node.js apps on aaPanel, some processes occasionally die without notice. This plugin:

1. Detects which project stopped
2. Distinguishes a crash from a manual Stop in the panel
3. Sends an SMTP email to a default address (or a per-project address)
4. Attaches recent log lines and simple hints (OOM, `EADDRINUSE`, missing module, …)

---

## Features

- Auto-discovers Node.js projects from aaPanel’s site database
- Background systemd service (`node_watchdog`) with configurable check interval
- Crash vs manual-stop detection (PID file still present → crash; PID file removed → stopped from panel)
- Confirm delay before alerting (avoids false alarms when you Restart from aaPanel)
- Email routing:
  - Default email for all projects
  - Optional per-project email(s)
  - Optional CC to the default email when a project has its own address
- Alert content: timestamps, PID(s), memory, probable cause, kernel OOM lines, last N log lines
- Optional notifications: recovery, manual stop, PID change (restart), repeat reminders
- Panel UI: project list, SMTP settings, monitoring options, event log
- Test alert per project (sends a sample crash email with real log tail)

---

## Requirements

- aaPanel on Linux
- Node.js projects managed by aaPanel (Website → Node project)
- Outbound SMTP access (Gmail / Outlook / Zoho / your own server)

> Projects started only via PM2 Manager (outside aaPanel Node projects) are not monitored.

---

## Install

### Option A — Import in aaPanel (recommended)

1. Download / zip this folder so that these files sit at the **root** of the archive (not inside an extra nested folder):

   ```
   icon.png
   index.html
   info.json
   install.sh
   node_watchdog_core.py
   node_watchdog_daemon.py
   node_watchdog_main.py
   ```

2. aaPanel → **App Store** → **Third-party** → **Import plugin**
3. Upload the zip and install
4. Open **Node.js Watchdog** and configure SMTP

`install.sh` creates and starts the `node_watchdog` systemd service.

### Option B — SSH

```bash
# as root
mkdir -p /www/server/panel/plugin/node_watchdog
cd /www/server/panel/plugin/node_watchdog
# copy plugin files here, then:
bash install.sh install
systemctl status node_watchdog
```

Uninstall:

```bash
bash /www/server/panel/plugin/node_watchdog/install.sh uninstall
```

Or use **Uninstall** in the App Store.

---

## First-time setup

1. **Email / SMTP** — host, port, SSL/STARTTLS, username, password, From address, default recipient  
   - Gmail / Outlook with 2FA: use an [App Password](https://support.google.com/accounts/answer/185833)
2. Click **Save & send test email**
3. **Projects** — confirm every Node.js project appears with the correct Running/Stopped status  
   - Set a custom email per project if needed  
   - Click **Test alert** to verify log detection
4. **Settings** — ensure the watchdog service shows **Running**

---

## How it works

| Step | Detail |
|------|--------|
| Discover | Reads aaPanel DB (`site.db` / `default.db`) for `project_type = Node` |
| Probe | Reads `/www/server/nodejs/vhost/pids/<name>.pid` and checks `/proc` |
| Confirm | Waits `confirm_delay` seconds and re-checks before sending a crash alert |
| Diagnose | Tails project log, scans for common error patterns, checks `dmesg` for OOM kills |
| Notify | Sends HTML + plain-text email via SMTP; retries on failure (queued in state) |

### Email routing

| Project email | Default email | Result |
|---------------|---------------|--------|
| empty | set | → default |
| set | set | → project only (or project + default if “CC default” is on) |
| empty | empty | no email (logged as skipped) |

---

## Settings (defaults)

| Setting | Default | Meaning |
|---------|---------|---------|
| Check interval | 30 s | How often projects are scanned (min 10) |
| Confirm delay | 5 s | Re-check before crash alert |
| Log lines in email | 60 | Tail size attached to alerts |
| Repeat alert | 0 min | `0` = once per crash; otherwise remind every N minutes |
| Notify recovery | on | Email when a crashed project is running again |
| Notify manual stop | off | Email when stopped from aaPanel |
| Notify restart | off | Email when PID changes while “running” |

Runtime config is stored next to the plugin as `config.json` (created after you save settings). Do not commit real SMTP passwords.

---

## Panel tabs

| Tab | Purpose |
|-----|---------|
| **Projects** | Status, per-project email / log path, Test alert |
| **Email / SMTP** | SMTP server + default recipient |
| **Settings** | Interval, notification toggles, start/stop service |
| **Event log** | Crashes, emails sent, SMTP errors |

---

## Project layout

```
node_watchdog/
├── info.json                 # aaPanel plugin metadata
├── install.sh                # install / uninstall + systemd unit
├── icon.png
├── index.html                # panel UI
├── node_watchdog_main.py     # panel API entrypoint
├── node_watchdog_core.py     # discovery, probe, crash analysis, SMTP
├── node_watchdog_daemon.py   # background loop
└── README.md
```

Runtime files (created on the server, not shipped):

- `config.json` — SMTP & options  
- `state.json` — last known process state / pending emails  
- `events.log` — plugin event history  
- `daemon.pid` / `daemon.lock`

---

## Packaging for aaPanel

Text files must use **LF** line endings (CRLF breaks `install.sh` on Linux). Zip files at the **archive root** (no nested `node_watchdog/` folder inside the zip).

```bash
cd node_watchdog
zip -r ../node_watchdog.zip \
  icon.png index.html info.json install.sh \
  node_watchdog_core.py node_watchdog_daemon.py node_watchdog_main.py
```

Do not include runtime files (`config.json`, `state.json`, `events.log`, `*.pid`, `*.lock`).

---

## Limitations

- Only aaPanel **Node.js projects** (not arbitrary PM2 / systemd Node apps)
- Does not detect hung processes that are still alive
- Manual-stop detection assumes aaPanel removes the PID file on Stop
- Paths assumed by default:
  - PID: `/www/server/nodejs/vhost/pids/`
  - Logs: `/www/server/nodejs/vhost/logs/` (overridable per project)

---

## Security notes

- SMTP password is stored in `config.json` on the server (`chmod 600` after install)
- Restrict panel access; anyone who can open the plugin can change SMTP settings
- Prefer an SMTP account limited to sending (App Password), not your main login password

---

## Contributing

Issues and pull requests are welcome. When reporting a bug, include aaPanel version, OS, and a redacted snippet from **Event log** (no passwords).

---

## License

[MIT](LICENSE) © 2026 Tuyen Pham
