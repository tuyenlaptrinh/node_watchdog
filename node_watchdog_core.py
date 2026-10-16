# coding: utf-8
"""Shared logic for the Node.js Watchdog aaPanel plugin (used by the panel UI and the daemon)."""
import base64
import html
import json
import os
import re
import smtplib
import socket
import sqlite3
import ssl
import subprocess
import sys
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid

PLUGIN_NAME = 'node_watchdog'
PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(PLUGIN_DIR, 'config.json')
STATE_FILE = os.path.join(PLUGIN_DIR, 'state.json')
EVENTS_FILE = os.path.join(PLUGIN_DIR, 'events.log')
DAEMON_PID_FILE = os.path.join(PLUGIN_DIR, 'daemon.pid')
DAEMON_SCRIPT = os.path.join(PLUGIN_DIR, 'node_watchdog_daemon.py')
SERVICE_NAME = 'node_watchdog'
SERVICE_FILE = '/etc/systemd/system/%s.service' % SERVICE_NAME

PANEL_DIR = '/www/server/panel'
PANEL_PYTHON = PANEL_DIR + '/pyenv/bin/python3'
SITE_DBS = (PANEL_DIR + '/data/db/site.db', PANEL_DIR + '/data/default.db')
NODE_PID_DIR = '/www/server/nodejs/vhost/pids'
NODE_LOG_DIR = '/www/server/nodejs/vhost/logs'

EVENTS_MAX_BYTES = 2 * 1024 * 1024
EVENTS_KEEP_LINES = 2000
MAIL_MAX_ATTEMPTS = 6
EMAIL_RE = re.compile(r'^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$')
ANSI_RE = re.compile(r'\x1b\[[0-9;?]*[ -/]*[@-~]')
CLK_TCK = os.sysconf('SC_CLK_TCK') if hasattr(os, 'sysconf') else 100

DEFAULT_CONFIG = {
    'smtp': {
        'host': '',
        'port': 465,
        'security': 'ssl',
        'username': '',
        'password': '',
        'from_email': '',
        'from_name': 'aaPanel Node Watchdog',
    },
    'default_email': '',
    'cc_default': False,
    'interval': 30,
    'confirm_delay': 5,
    'log_lines': 60,
    'repeat_minutes': 0,
    'notify_recovery': True,
    'notify_manual_stop': False,
    'notify_restart': False,
    'projects': {},
}

LOG_HINTS = (
    (r'JavaScript heap out of memory|Allocation failed - JavaScript heap',
     'Node.js ran out of heap memory (memory leak, or raise --max-old-space-size).'),
    (r'EADDRINUSE', 'The port is already in use by another process.'),
    (r'Cannot find module|MODULE_NOT_FOUND', 'A required module is missing (run npm install / check the build output).'),
    (r'UnhandledPromiseRejection|unhandled promise rejection|triggerUncaughtException',
     'An unhandled exception / promise rejection crashed the process.'),
    (r'ECONNREFUSED', 'A connection was refused (database, Redis or upstream service down?).'),
    (r'SyntaxError', 'A syntax error was thrown while loading the code.'),
    (r'EACCES|permission denied', 'Permission denied while accessing a file or port.'),
    (r'ENOSPC', 'No space left on device (or too many file watchers).'),
    (r'EMFILE|too many open files', 'Too many open files (file descriptor limit reached).'),
    (r'Segmentation fault|SIGSEGV', 'Native crash (segmentation fault).'),
    (r'npm ERR!', 'npm reported an error while running the start script.'),
)


# ---------------------------------------------------------------- storage

def read_json(path, default):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (IOError, OSError, ValueError):
        return default


def write_json(path, data, mode=0o600):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def load_config():
    data = read_json(CONFIG_FILE, {})
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if isinstance(data, dict):
        for key, value in data.items():
            if key == 'smtp' and isinstance(value, dict):
                cfg['smtp'].update(value)
            else:
                cfg[key] = value
    if not isinstance(cfg.get('projects'), dict):
        cfg['projects'] = {}
    return cfg


def save_config(cfg):
    write_json(CONFIG_FILE, cfg)


def project_watch_cfg(cfg, name):
    watch = cfg['projects'].get(name)
    return watch if isinstance(watch, dict) else {}


def add_event(project, event, detail='', level='info'):
    record = {'time': int(time.time()), 'project': project, 'event': event,
              'detail': detail, 'level': level}
    try:
        with open(EVENTS_FILE, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
        if os.path.getsize(EVENTS_FILE) > EVENTS_MAX_BYTES:
            with open(EVENTS_FILE, 'r', encoding='utf-8', errors='replace') as f:
                lines = f.readlines()[-EVENTS_KEEP_LINES:]
            with open(EVENTS_FILE, 'w', encoding='utf-8') as f:
                f.writelines(lines)
    except (IOError, OSError):
        pass


def read_events(limit=300):
    events = []
    for line in tail_file(EVENTS_FILE, limit, max_bytes=1024 * 1024, clean=False).split('\n'):
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    events.reverse()
    return events


# ---------------------------------------------------------------- helpers

def run_cmd(cmd, timeout=15):
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
        return proc.returncode, proc.stdout.decode('utf-8', 'replace')
    except (OSError, subprocess.SubprocessError) as e:
        return -1, str(e)


def b64decode_text(value):
    if not value:
        return ''
    try:
        return base64.b64decode(value).decode('utf-8')
    except (ValueError, UnicodeDecodeError):
        return ''


def fmt_time(ts):
    if not ts:
        return '-'
    return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts)) + time.strftime(' (%z)')


def fmt_duration(seconds):
    if seconds is None or seconds < 0:
        return '-'
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append('%dd' % days)
    if hours:
        parts.append('%dh' % hours)
    if minutes:
        parts.append('%dm' % minutes)
    if secs or not parts:
        parts.append('%ds' % secs)
    return ' '.join(parts)


def fmt_kb(kb):
    if not kb:
        return '-'
    if kb >= 1024 * 1024:
        return '%.2f GB' % (kb / 1024.0 / 1024.0)
    if kb >= 1024:
        return '%.1f MB' % (kb / 1024.0)
    return '%d KB' % kb


def parse_emails(text):
    return [e for e in re.split(r'[\s,;]+', text or '') if EMAIL_RE.match(e)]


def invalid_emails(text):
    return [e for e in re.split(r'[\s,;]+', text or '') if e and not EMAIL_RE.match(e)]


def recipients_for(cfg, name):
    own = parse_emails(project_watch_cfg(cfg, name).get('email'))
    default = parse_emails(cfg.get('default_email'))
    if not own:
        return default
    if cfg.get('cc_default'):
        own += [e for e in default if e not in own]
    return own


def server_label():
    host = socket.gethostname()
    ip = ''
    try:
        with open(PANEL_DIR + '/data/iplist.txt', 'r') as f:
            ip = f.read().strip().split()[0]
    except (IOError, OSError, IndexError):
        pass
    return '%s (%s)' % (host, ip) if ip else host


def tail_file(path, lines=50, max_bytes=256 * 1024, clean=True):
    try:
        with open(path, 'rb') as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            data = f.read()
    except (IOError, OSError):
        return ''
    text = data.decode('utf-8', 'replace').replace('\r\n', '\n')
    if clean:
        text = ANSI_RE.sub('', text)
    rows = text.rstrip('\n').split('\n')
    if size > max_bytes and len(rows) > 1:
        rows = rows[1:]
    return '\n'.join(rows[-lines:]) if lines > 0 else ''


def detect_hints(log_text):
    return [hint for pattern, hint in LOG_HINTS if re.search(pattern, log_text or '', re.I)]


def find_oom_kills(pids):
    pids = set(p for p in pids if p)
    if not pids:
        return []
    code, out = run_cmd(['dmesg', '-T'])
    if code != 0:
        code, out = run_cmd(['dmesg'])
    if code != 0:
        return []
    hits = []
    for line in out.splitlines()[-3000:]:
        m = re.search(r'Killed process (\d+)', line)
        if m and int(m.group(1)) in pids:
            hits.append(line.strip())
    return hits[-5:]


# ---------------------------------------------------------------- aaPanel projects & processes

def list_node_projects():
    """Return {name: {'name', 'path', 'ps', 'config'}} for every Node.js project in aaPanel."""
    projects = {}
    for db in SITE_DBS:
        if not os.path.isfile(db):
            continue
        try:
            conn = sqlite3.connect('file:%s?mode=ro' % db, uri=True, timeout=5)
            try:
                rows = conn.execute(
                    "SELECT name, path, project_config, ps FROM sites "
                    "WHERE LOWER(project_type) IN ('node', 'nodejs')").fetchall()
            finally:
                conn.close()
        except sqlite3.Error:
            continue
        for name, path, raw_cfg, ps in rows:
            if not name or name in projects:
                continue
            try:
                pcfg = json.loads(raw_cfg or '{}')
            except ValueError:
                pcfg = {}
            if not isinstance(pcfg, dict):
                pcfg = {}
            projects[name] = {'name': name, 'path': path or pcfg.get('project_cwd', ''),
                              'ps': ps or '', 'config': pcfg}
    return projects


def boot_time():
    try:
        with open('/proc/stat', 'r') as f:
            for line in f:
                if line.startswith('btime '):
                    return int(line.split()[1])
    except (IOError, OSError, ValueError):
        pass
    return 0


def read_boot_id():
    try:
        with open('/proc/sys/kernel/random/boot_id', 'r') as f:
            return f.read().strip()
    except (IOError, OSError):
        return str(boot_time())


class ProcSnapshot(object):
    """One pass over /proc so that every project is checked against the same process table."""

    def __init__(self):
        self.table = {}
        self.children = {}
        self.btime = boot_time()
        try:
            entries = os.listdir('/proc')
        except OSError:
            entries = []
        for entry in entries:
            if not entry.isdigit():
                continue
            try:
                with open('/proc/%s/stat' % entry, 'r') as f:
                    data = f.read()
                fields = data[data.rindex(')') + 2:].split()
                self.table[int(entry)] = (fields[0], int(fields[1]), int(fields[19]))
            except (IOError, OSError, ValueError, IndexError):
                continue
        for pid, (_, ppid, _) in self.table.items():
            self.children.setdefault(ppid, []).append(pid)

    def alive(self, pid):
        info = self.table.get(pid)
        return bool(info) and info[0] not in ('Z', 'X', 'x')

    def tree(self, root):
        seen, stack = [], [root]
        while stack:
            pid = stack.pop()
            if pid in seen:
                continue
            seen.append(pid)
            stack.extend(self.children.get(pid, []))
        return [p for p in seen if self.alive(p)]

    def started_at(self, pid):
        info = self.table.get(pid)
        if not info or not self.btime:
            return None
        return int(self.btime + info[2] / float(CLK_TCK))


def rss_kb(pids):
    total = 0
    for pid in pids:
        try:
            with open('/proc/%d/status' % pid, 'r') as f:
                for line in f:
                    if line.startswith('VmRSS:'):
                        total += int(line.split()[1])
                        break
        except (IOError, OSError, ValueError, IndexError):
            continue
    return total


def pid_file_path(name):
    return os.path.join(NODE_PID_DIR, name + '.pid')


def probe_project(name, snap):
    path = pid_file_path(name)
    result = {'pid_file': os.path.exists(path), 'pid': None, 'running': False,
              'pids': [], 'rss_kb': 0, 'started_at': None}
    if not result['pid_file']:
        return result
    try:
        with open(path, 'r') as f:
            result['pid'] = int(f.read().strip().split()[0])
    except (IOError, OSError, ValueError, IndexError):
        return result
    if snap.alive(result['pid']):
        pids = snap.tree(result['pid'])
        result.update(running=True, pids=pids, rss_kb=rss_kb(pids),
                      started_at=snap.started_at(result['pid']))
    return result


def auto_log_file(name, project_cfg):
    candidates = []
    log_path = str(project_cfg.get('log_path') or '').strip()
    if log_path:
        candidates.append(os.path.join(log_path, name + '.log') if os.path.isdir(log_path) else log_path)
    candidates.append(os.path.join(NODE_LOG_DIR, name + '.log'))
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return candidates[-1]


def project_log_file(name, project_cfg, watch_cfg):
    custom = str(watch_cfg.get('log_file') or '').strip()
    return custom or auto_log_file(name, project_cfg)


# ---------------------------------------------------------------- email

def smtp_ready(cfg):
    smtp = cfg.get('smtp') or {}
    return bool(smtp.get('host') and (smtp.get('from_email') or smtp.get('username')))


def send_mail(smtp_cfg, recipients, subject, text_body, html_body=None):
    host = str(smtp_cfg.get('host') or '').strip()
    if not host:
        raise ValueError('SMTP server is not configured')
    if not recipients:
        raise ValueError('No recipient email address')
    security = smtp_cfg.get('security') or 'ssl'
    port = int(smtp_cfg.get('port') or 0) or {'ssl': 465, 'starttls': 587}.get(security, 25)
    username = str(smtp_cfg.get('username') or '').strip()
    sender = str(smtp_cfg.get('from_email') or '').strip() or username

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = formataddr((smtp_cfg.get('from_name') or 'aaPanel Node Watchdog', sender))
    msg['To'] = ', '.join(recipients)
    msg['Date'] = formatdate(localtime=True)
    msg['Message-ID'] = make_msgid()
    msg.attach(MIMEText(text_body, 'plain', 'utf-8'))
    if html_body:
        msg.attach(MIMEText(html_body, 'html', 'utf-8'))

    context = ssl.create_default_context()
    if security == 'ssl':
        server = smtplib.SMTP_SSL(host, port, timeout=30, context=context)
    else:
        server = smtplib.SMTP(host, port, timeout=30)
        server.ehlo()
        if security == 'starttls':
            server.starttls(context=context)
            server.ehlo()
    try:
        if username:
            server.login(username, smtp_cfg.get('password') or '')
        server.sendmail(sender, recipients, msg.as_string())
    finally:
        try:
            server.quit()
        except Exception:
            pass


def friendly_smtp_error(error):
    if isinstance(error, smtplib.SMTPAuthenticationError):
        return 'SMTP authentication failed - check username/password (Gmail/Outlook need an App Password).'
    if isinstance(error, (socket.timeout, TimeoutError)):
        return 'Timed out connecting to the SMTP server - check host/port and that the port is not blocked.'
    if isinstance(error, ssl.SSLError):
        return 'SSL error: %s - check that the encryption matches the port (465 = SSL, 587 = STARTTLS).' % error
    return '%s: %s' % (type(error).__name__, error)


EMAIL_KINDS = {
    'crash': ('ALERT', 'stopped unexpectedly', '#d9363e'),
    'reminder': ('STILL DOWN', 'is still down', '#d9363e'),
    'recovered': ('RECOVERED', 'is running again', '#20a53a'),
    'stopped': ('INFO', 'was stopped', '#e6a23c'),
    'restarted': ('RESTARTED', 'was restarted (PID changed)', '#e6a23c'),
}


def build_email(kind, name, rows, log_file=None, log_tail=None, test=False):
    tag, verb, color = EMAIL_KINDS[kind]
    server = server_label()
    subject = '[%s] Node.js project "%s" %s - %s' % (tag, name, verb, socket.gethostname())
    if test:
        subject = '[TEST] ' + subject
    rows = [('Project', name), ('Server', server)] + [(k, v) for k, v in rows if v not in (None, '')]

    text = ['Node.js project "%s" %s.' % (name, verb), '']
    if test:
        text.insert(0, 'THIS IS A TEST MESSAGE sent from the aaPanel Node Watchdog plugin.\n')
    width = max(len(k) for k, _ in rows)
    for key, value in rows:
        lines = str(value).split('\n')
        text.append('%s : %s' % (key.ljust(width), lines[0]))
        text.extend(' ' * (width + 3) + extra for extra in lines[1:])
    if log_file is not None:
        text += ['', '---- Last lines of %s ----' % log_file, log_tail or '(log file is empty or not readable)']

    esc = html.escape
    cells = ''.join(
        '<tr><td style="padding:6px 10px;background:#f7f7f7;font-weight:bold;white-space:nowrap;'
        'vertical-align:top;border:1px solid #e5e5e5">%s</td><td style="padding:6px 10px;'
        'border:1px solid #e5e5e5;white-space:pre-wrap">%s</td></tr>' % (esc(k), esc(str(v)))
        for k, v in rows)
    log_html = ''
    if log_file is not None:
        log_html = (
            '<h3 style="margin:20px 0 8px;font-size:14px">Last lines of <code>%s</code></h3>'
            '<pre style="background:#1e1e1e;color:#d4d4d4;padding:12px;border-radius:4px;font-size:12px;'
            'line-height:1.45;white-space:pre-wrap;word-break:break-all">%s</pre>'
            % (esc(log_file), esc(log_tail or '(log file is empty or not readable)')))
    test_html = ('<p style="background:#fff3cd;padding:8px 12px;border-radius:4px">'
                 'This is a <b>test message</b> sent from the aaPanel Node Watchdog plugin.</p>' if test else '')
    html_body = (
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:13px;color:#333;max-width:900px">'
        '%s<div style="background:%s;color:#fff;padding:12px 16px;border-radius:4px 4px 0 0;font-size:16px">'
        '<b>[%s]</b> Node.js project <b>%s</b> %s</div>'
        '<table style="border-collapse:collapse;width:100%%;margin-top:0">%s</table>%s'
        '<p style="color:#999;font-size:11px;margin-top:20px">Sent by aaPanel Node Watchdog on %s</p></div>'
        % (test_html, color, esc(tag), esc(name), esc(verb), cells, log_html, esc(server)))
    return subject, '\n'.join(text), html_body


# ---------------------------------------------------------------- watchdog

class Watchdog(object):
    def __init__(self):
        state = read_json(STATE_FILE, {})
        self.state = state if isinstance(state, dict) else {}
        self.state.setdefault('projects', {})
        self.state.setdefault('pending', [])

    def save(self):
        write_json(STATE_FILE, self.state)

    def run_once(self):
        cfg = load_config()
        now = time.time()
        projects = list_node_projects()
        snap = ProcSnapshot()
        boot_id = read_boot_id()
        rebooted = bool(self.state.get('boot_id')) and self.state.get('boot_id') != boot_id
        self.state['boot_id'] = boot_id
        tracked = self.state['projects']
        suspects = []

        for name, project in projects.items():
            st = probe_project(name, snap)
            prev = tracked.get(name)
            if prev is None:
                tracked[name] = self._baseline(st, now)
            elif st['running']:
                self._on_running(cfg, name, project, prev, st, now)
            elif prev.get('running'):
                suspects.append(name)
            else:
                self._maybe_remind(cfg, name, prev, now)

        if suspects:
            # Re-check after a short delay so a stop/start from the panel is not reported as a crash.
            delay = max(0, min(60, int(cfg.get('confirm_delay') or 0)))
            if delay and not rebooted:
                time.sleep(delay)
                snap = ProcSnapshot()
            for name in suspects:
                st = probe_project(name, snap)
                if st['running']:
                    self._on_running(cfg, name, projects[name], tracked[name], st, now)
                else:
                    self._on_down(cfg, name, projects[name], tracked[name], st, now, rebooted)

        for name in list(tracked):
            if name not in projects:
                del tracked[name]
        self.state['last_check'] = int(time.time())
        self.state['project_count'] = len(projects)
        self.flush(cfg)
        self.save()

    @staticmethod
    def _baseline(st, now):
        return {'running': st['running'], 'pid': st['pid'], 'pids': st['pids'], 'rss_kb': st['rss_kb'],
                'peak_rss_kb': st['rss_kb'], 'started_at': st['started_at'],
                'last_seen': now if st['running'] else None, 'down_since': None,
                'down_kind': None, 'alerted': False, 'last_alert': 0}

    def _on_running(self, cfg, name, project, prev, st, now):
        enabled = project_watch_cfg(cfg, name).get('enabled', True)
        pid_changed = prev.get('running') and prev.get('pid') and st['pid'] != prev.get('pid')
        if pid_changed:
            add_event(name, 'restarted', 'PID %s -> %s' % (prev.get('pid'), st['pid']), 'warning')
            if enabled and cfg.get('notify_restart'):
                log_file = project_log_file(name, project['config'], project_watch_cfg(cfg, name))
                self.queue(cfg, name, 'restarted', [
                    ('Detected at', fmt_time(now)),
                    ('Old PID', prev.get('pid')),
                    ('New PID', st['pid']),
                    ('Previous uptime', fmt_duration((prev.get('last_seen') or now) - (prev.get('started_at') or now))),
                    ('Previous memory (last / peak)', '%s / %s' % (fmt_kb(prev.get('rss_kb')), fmt_kb(prev.get('peak_rss_kb')))),
                ], log_file, tail_file(log_file, int(cfg.get('log_lines') or 60)))
        if not prev.get('running'):
            add_event(name, 'running', 'PID %s' % st['pid'])
            if enabled and prev.get('alerted') and cfg.get('notify_recovery'):
                self.queue(cfg, name, 'recovered', [
                    ('Recovered at', fmt_time(now)),
                    ('Down since', fmt_time(prev.get('down_since'))),
                    ('Downtime', fmt_duration(now - prev['down_since']) if prev.get('down_since') else '-'),
                    ('New PID', st['pid']),
                ])
            prev.update(down_since=None, down_kind=None, alerted=False, last_alert=0)
        same_process = prev.get('running') and not pid_changed
        prev.update(running=True, pid=st['pid'], pids=st['pids'], rss_kb=st['rss_kb'],
                    peak_rss_kb=max(prev.get('peak_rss_kb') or 0, st['rss_kb']) if same_process else st['rss_kb'],
                    started_at=st['started_at'], last_seen=now)

    def _on_down(self, cfg, name, project, prev, st, now, rebooted):
        watch = project_watch_cfg(cfg, name)
        enabled = watch.get('enabled', True)
        log_file = project_log_file(name, project['config'], watch)
        log_lines = int(cfg.get('log_lines') or 60)
        prev.update(running=False, down_since=now, alerted=False, last_alert=0)

        if not st['pid_file'] and not rebooted:
            prev['down_kind'] = 'stopped'
            add_event(name, 'stopped', 'PID file removed - stopped from aaPanel or by a user.')
            if enabled and cfg.get('notify_manual_stop'):
                self.queue(cfg, name, 'stopped', [
                    ('Detected at', fmt_time(now)),
                    ('Last seen running', fmt_time(prev.get('last_seen'))),
                    ('Note', 'The PID file was removed, which usually means the project was stopped from aaPanel.'),
                ], log_file, tail_file(log_file, log_lines))
                prev.update(alerted=True, last_alert=now)
            return

        prev['down_kind'] = 'crash'
        details = self.crash_details(cfg, name, project, prev, now, rebooted)
        add_event(name, 'crashed', '; '.join(details['causes'] + details['hints']), 'error')
        if enabled:
            self.queue(cfg, name, 'crash', details['rows'], details['log_file'], details['log_tail'])
            prev.update(alerted=True, last_alert=now)

    def _maybe_remind(self, cfg, name, prev, now):
        repeat = int(cfg.get('repeat_minutes') or 0)
        if repeat <= 0 or prev.get('down_kind') != 'crash' or not prev.get('alerted'):
            return
        if not project_watch_cfg(cfg, name).get('enabled', True):
            return
        if now - (prev.get('last_alert') or 0) < repeat * 60:
            return
        self.queue(cfg, name, 'reminder', [
            ('Down since', fmt_time(prev.get('down_since'))),
            ('Downtime', fmt_duration(now - (prev.get('down_since') or now))),
            ('Note', 'You will be reminded every %d minute(s) until the project is running again.' % repeat),
        ])
        prev['last_alert'] = now

    @staticmethod
    def crash_details(cfg, name, project, prev, now, rebooted=False):
        pcfg = project.get('config') or {}
        watch = project_watch_cfg(cfg, name)
        pids = set(prev.get('pids') or [])
        if prev.get('pid'):
            pids.add(prev['pid'])

        causes = []
        oom = [] if rebooted else find_oom_kills(pids)
        if rebooted:
            causes.append('The server was rebooted, so every process was stopped.')
        if oom:
            causes.append('Killed by the Linux OOM killer - the server ran out of memory.')

        log_file = project_log_file(name, pcfg, watch)
        log_tail = tail_file(log_file, int(cfg.get('log_lines') or 60))
        hints = detect_hints(log_tail)
        peak = prev.get('peak_rss_kb') or 0
        try:
            limit_mb = int(pcfg.get('max_memory_limit') or 0)
        except (TypeError, ValueError):
            limit_mb = 0
        if limit_mb and peak >= limit_mb * 1024 * 0.9:
            hints.append('Memory reached %s, close to the project limit of %d MB.' % (fmt_kb(peak), limit_mb))
        if not causes:
            causes.append('The process exited by itself (uncaught error, process.exit or killed). See the log below.')

        log_mtime = None
        try:
            log_mtime = os.path.getmtime(log_file)
        except OSError:
            pass
        last_seen = prev.get('last_seen')
        started = prev.get('started_at')
        rows = [
            ('Detected at', fmt_time(now)),
            ('Last seen running', fmt_time(last_seen)),
            ('Log last written', fmt_time(log_mtime)),
            ('Process started at', fmt_time(started)),
            ('Uptime before stop', fmt_duration(last_seen - started) if last_seen and started else '-'),
            ('Last PID(s)', ', '.join(str(p) for p in sorted(pids)) or '-'),
            ('Memory (last / peak)', '%s / %s' % (fmt_kb(prev.get('rss_kb')), fmt_kb(peak))),
            ('Probable cause', '\n'.join(causes)),
            ('Hints from log', '\n'.join(hints)),
            ('Kernel log', '\n'.join(oom)),
            ('Project path', project.get('path') or pcfg.get('project_cwd')),
            ('Start script', pcfg.get('project_script')),
        ]
        return {'causes': causes, 'hints': hints, 'rows': rows, 'log_file': log_file, 'log_tail': log_tail}

    def queue(self, cfg, name, kind, rows, log_file=None, log_tail=None):
        recipients = recipients_for(cfg, name)
        if not recipients:
            add_event(name, 'email_skipped', 'No recipient: set a default email or a project email.', 'warning')
            return
        subject, text, html_body = build_email(kind, name, rows, log_file, log_tail)
        self.state['pending'].append({'project': name, 'kind': kind, 'to': recipients, 'subject': subject,
                                      'text': text, 'html': html_body, 'attempts': 0,
                                      'created': time.time(), 'next_try': 0})

    def flush(self, cfg):
        keep = []
        now = time.time()
        for item in self.state.get('pending') or []:
            if item.get('next_try', 0) > now:
                keep.append(item)
                continue
            try:
                send_mail(cfg['smtp'], item['to'], item['subject'], item['text'], item['html'])
                add_event(item['project'], 'email_sent', '%s -> %s' % (item['kind'], ', '.join(item['to'])))
            except Exception as e:
                item['attempts'] = item.get('attempts', 0) + 1
                give_up = item['attempts'] >= MAIL_MAX_ATTEMPTS or now - item.get('created', now) > 6 * 3600
                add_event(item['project'], 'email_failed', '%s (attempt %d%s)' % (
                    friendly_smtp_error(e), item['attempts'], ', giving up' if give_up else ''), 'error')
                if not give_up:
                    item['next_try'] = now + 60 * item['attempts']
                    keep.append(item)
        self.state['pending'] = keep


# ---------------------------------------------------------------- daemon management

def python_bin():
    return PANEL_PYTHON if os.path.exists(PANEL_PYTHON) else sys.executable


def has_systemd():
    return os.path.isdir('/run/systemd/system')


def daemon_pid():
    try:
        with open(DAEMON_PID_FILE, 'r') as f:
            pid = int(f.read().strip())
        with open('/proc/%d/cmdline' % pid, 'rb') as f:
            if b'node_watchdog_daemon' in f.read():
                return pid
    except (IOError, OSError, ValueError):
        pass
    return None


def install_service():
    unit = (
        '[Unit]\n'
        'Description=aaPanel Node.js Watchdog\n'
        'After=network-online.target\n\n'
        '[Service]\n'
        'Type=simple\n'
        'ExecStart=%s %s\n'
        'Restart=always\n'
        'RestartSec=5\n\n'
        '[Install]\n'
        'WantedBy=multi-user.target\n' % (python_bin(), DAEMON_SCRIPT))
    with open(SERVICE_FILE, 'w') as f:
        f.write(unit)
    run_cmd(['systemctl', 'daemon-reload'])
    run_cmd(['systemctl', 'enable', SERVICE_NAME])


def daemon_status():
    pid = daemon_pid()
    mode = 'systemd' if has_systemd() else 'background process'
    return {'running': bool(pid), 'pid': pid, 'mode': mode}


def control_daemon(action):
    if action not in ('start', 'stop', 'restart'):
        return False, 'Unknown action'
    if has_systemd():
        if not os.path.exists(SERVICE_FILE):
            install_service()
        code, out = run_cmd(['systemctl', action, SERVICE_NAME])
        if code != 0:
            return False, out.strip() or 'systemctl %s failed' % action
    else:
        pid = daemon_pid()
        if pid and action in ('stop', 'restart'):
            try:
                os.kill(pid, 15)
            except OSError:
                pass
            for _ in range(20):
                if not daemon_pid():
                    break
                time.sleep(0.25)
        if action in ('start', 'restart') and not daemon_pid():
            with open(os.devnull, 'wb') as devnull:
                subprocess.Popen([python_bin(), DAEMON_SCRIPT], stdout=devnull, stderr=devnull,
                                 stdin=devnull, close_fds=True, start_new_session=True)
    time.sleep(1.5)
    running = bool(daemon_pid())
    if action == 'stop':
        return not running, 'Watchdog stopped' if not running else 'Failed to stop the watchdog'
    return running, 'Watchdog is running' if running else 'Watchdog failed to start, check %s' % EVENTS_FILE
