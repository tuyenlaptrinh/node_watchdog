# Node.js Watchdog

Third-party [aaPanel](https://www.aapanel.com/) plugin that watches **Node.js projects** and **system services** (Nginx, databases, PHP-FPM versions, optionally aaPanel itself). When something stops unexpectedly, it emails you via SMTP — with log context for Node apps.

**Version:** 1.1.0  
**Author:** [Tuyen Pham](https://github.com/tuyenlaptrinh)  
**Platform:** Linux + aaPanel  
**Repo:** https://github.com/tuyenlaptrinh/node_watchdog

---

## Why

If you host many Node.js apps on aaPanel, processes and stack services occasionally die without notice. This plugin:

1. Detects which Node.js project stopped
2. Distinguishes a crash from a manual Stop in the panel
3. Optionally watches Nginx, MySQL/MariaDB, PostgreSQL, each installed PHP-FPM version, and aaPanel (`bt status`)
4. Sends an SMTP email to a default address (or a per-project address)
5. Attaches recent Node log lines and simple hints (OOM, `EADDRINUSE`, missing module, …)
6. Lets you **pause all checks** temporarily during maintenance

---

## Features

- Auto-discovers Node.js projects from aaPanel’s site database
- Auto-discovers stack services: Nginx, Apache (if present), MySQL/MariaDB, PostgreSQL, PHP-FPM per version, aaPanel
- Background systemd service (`node_watchdog`) with configurable check interval
- Crash vs manual-stop detection for Node projects
- Confirm delay before alerting (avoids false alarms on Restart)
- **Pause / Resume** all monitoring with one click
- Email routing:
  - Default email for projects without a custom address **and** for all system-service alerts
  - Optional per-project email(s)
  - Optional CC to the default email when a project has its own address
- Optional notifications: recovery, manual stop, PID change (restart), repeat reminders
- Panel UI: Projects, Services, SMTP, Settings, Event log
- Test alert per Node project

---

## Requirements

- aaPanel on Linux
- Node.js projects managed by aaPanel (Website → Node project), and/or stack services installed via aaPanel
- Outbound SMTP access (Gmail / Outlook / Zoho / your own server)

> Projects started only via PM2 Manager (outside aaPanel Node projects) are not monitored.

---

## Install

### Option A — Import in aaPanel (recommended)

1. Download a release zip (or zip this folder) so these files sit at the **root** of the archive:

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
3. **Projects** — confirm Node.js projects appear with the correct status; set custom emails if needed
4. **Services** — enable/disable Nginx, DB, PHP versions, aaPanel; use **Pause all checks** during maintenance
5. **Settings** — ensure the watchdog daemon shows **Running**

---

## How it works

| Step | Detail |
|------|--------|
| Discover projects | Reads aaPanel DB (`site.db` / `default.db`) for `project_type = Node` |
| Discover services | Looks for `/etc/init.d/{nginx,mysqld,pgsql,php-fpm-XX}` and `bt status` |
| Probe | Node: PID files under `/www/server/nodejs/vhost/pids/`; services: init.d status + PID files |
| Confirm | Waits `confirm_delay` seconds and re-checks before sending a crash alert |
| Pause | When paused, the daemon skips all probes until you resume |
| Notify | HTML + plain-text email via SMTP; retries on failure |

### Email routing

| Target | Recipient |
|--------|-----------|
| Node project with custom email | that address (+ default if “CC default” is on) |
| Node project without custom email | default email |
| System service (Nginx, DB, PHP, aaPanel) | **default email only** |

---

## Settings (defaults)

| Setting | Default | Meaning |
|---------|---------|---------|
| Check interval | 30 s | How often projects/services are scanned (min 10) |
| Confirm delay | 5 s | Re-check before crash alert |
| Log lines in email | 60 | Tail size attached to Node alerts |
| Repeat alert | 0 min | `0` = once per crash; otherwise remind every N minutes |
| Pause checks | off | Temporarily stop **all** monitoring |
| Notify recovery | on | Email when a crashed project/service is running again |
| Notify manual stop | off | Email when a Node project is stopped from aaPanel |
| Notify restart | off | Email when a Node project PID changes while “running” |

**Services tab defaults:** Nginx / DB / PHP versions = **on** when installed; aaPanel = **off** (opt-in).

Runtime config is stored as `config.json`. Do not commit real SMTP passwords.

---

## Panel tabs

| Tab | Purpose |
|-----|---------|
| **Projects** | Node.js status, per-project email / log path, Test alert, Pause |
| **Services** | Nginx, databases, PHP versions, aaPanel — enable/disable + Pause |
| **Email / SMTP** | SMTP server + default recipient |
| **Settings** | Interval, notification toggles, start/stop watchdog daemon |
| **Event log** | Crashes, emails sent, SMTP errors, pause/resume |

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
├── LICENSE
└── README.md
```

Runtime files (created on the server, not shipped):

- `config.json` — SMTP, options, per-service toggles, pause flag  
- `state.json` — last known process/service state / pending emails  
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
- Manual-stop detection assumes aaPanel removes the Node PID file on Stop
- Service detection relies on aaPanel’s `/etc/init.d/*` scripts and usual paths under `/www/server/`

---

## Security notes

- SMTP password is stored in `config.json` on the server (`chmod 600` after install)
- Restrict panel access; anyone who can open the plugin can change SMTP settings
- Prefer an SMTP account limited to sending (App Password), not your main login password

---

## Changelog

### 1.1.0

- Watch system services: Nginx, MySQL/MariaDB, PostgreSQL, PHP-FPM (each installed version), optional aaPanel (`bt status`)
- New **Services** tab with per-service enable/disable
- **Pause / Resume** all monitoring (projects + services)
- Migrate legacy `watch_panel` config into `services.panel`

### 1.0.0

- Initial release: Node.js project crash detection + SMTP alerts

---

## Contributing

Issues and pull requests are welcome. When reporting a bug, include aaPanel version, OS, and a redacted snippet from **Event log** (no passwords).

---

## License

[MIT](LICENSE) © 2026 Tuyen Pham
