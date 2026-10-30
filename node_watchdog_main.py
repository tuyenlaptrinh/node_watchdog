# coding: utf-8
"""aaPanel entry point: every public method is callable from index.html via
/plugin?action=a&name=node_watchdog&s=<method>"""
import os
import sys
import time

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)
import node_watchdog_core as core  # noqa: E402


def _ok(msg='Success', data=None):
    result = {'status': True, 'msg': msg}
    if data is not None:
        result['data'] = data
    return result


def _err(msg):
    return {'status': False, 'msg': msg}


def _str(get, key, default=''):
    value = getattr(get, key, None)
    return default if value is None else str(value).strip()


def _int(get, key, default, low=None, high=None):
    try:
        value = int(_str(get, key, str(default)))
    except ValueError:
        value = default
    if low is not None:
        value = max(low, value)
    if high is not None:
        value = min(high, value)
    return value


def _bool(get, key):
    return _str(get, key).lower() in ('1', 'true', 'on', 'yes')


def _service_rows(cfg, state):
    tracked = state.get('services', {}) if isinstance(state, dict) else {}
    if not isinstance(tracked, dict):
        tracked = {}
    rows = []
    for svc in core.discover_services():
        default_on = svc['id'] != 'panel'
        enabled = core.service_enabled(cfg, svc['id'], default=default_on)
        live = core.probe_service(svc) if enabled else {'running': False, 'pid': None, 'detail': '', 'source': ''}
        prev = tracked.get(svc['id']) or {}
        crashed = enabled and (not live['running']) and prev.get('down_kind') == 'crash'
        rows.append({
            'id': svc['id'],
            'name': svc['name'],
            'kind': svc['kind'],
            'enabled': enabled,
            'running': bool(live.get('running')) if enabled else None,
            'pid': live.get('pid') if enabled else None,
            'source': live.get('source') if enabled else None,
            'detail': ((live.get('detail') or '')[:300] if enabled else ''),
            'crashed': crashed,
            'down_since': prev.get('down_since') if crashed else None,
        })
    return rows


class node_watchdog_main(object):

    def get_overview(self, get):
        cfg = core.load_config()
        state = core.read_json(core.STATE_FILE, {})
        tracked = state.get('projects', {}) if isinstance(state, dict) else {}
        snap = core.ProcSnapshot()
        rows = []
        for name, project in sorted(core.list_node_projects().items()):
            watch = core.project_watch_cfg(cfg, name)
            st = core.probe_project(name, snap)
            prev = tracked.get(name) or {}
            detected_log = core.auto_log_file(name, project['config'])
            crashed = not st['running'] and prev.get('down_kind') == 'crash'
            rows.append({
                'name': name,
                'ps': project['ps'],
                'path': project['path'],
                'running': st['running'],
                'pid': st['pid'],
                'memory': core.fmt_kb(st['rss_kb']),
                'enabled': watch.get('enabled', True),
                'email': watch.get('email', ''),
                'log_file': watch.get('log_file', ''),
                'detected_log': detected_log,
                'detected_log_exists': os.path.isfile(detected_log),
                'recipients': core.recipients_for(cfg, name),
                'crashed': crashed,
                'down_since': prev.get('down_since') if crashed else None,
            })
        return _ok(data={
            'projects': rows,
            'services': _service_rows(cfg, state),
            'service': core.daemon_status(),
            'last_check': state.get('last_check') if isinstance(state, dict) else None,
            'pending_emails': len(state.get('pending') or []) if isinstance(state, dict) else 0,
            'smtp_ready': core.smtp_ready(cfg),
            'default_email': cfg.get('default_email', ''),
            'watch_paused': bool(cfg.get('watch_paused')),
            'now': int(time.time()),
        })

    def get_services(self, get):
        cfg = core.load_config()
        state = core.read_json(core.STATE_FILE, {})
        return _ok(data={
            'services': _service_rows(cfg, state),
            'watch_paused': bool(cfg.get('watch_paused')),
            'default_email': cfg.get('default_email', ''),
            'smtp_ready': core.smtp_ready(cfg),
            'service': core.daemon_status(),
            'last_check': state.get('last_check') if isinstance(state, dict) else None,
        })

    def save_service(self, get):
        sid = _str(get, 'service_id')
        known = {s['id'] for s in core.discover_services()}
        if sid not in known:
            return _err('Service not found')
        cfg = core.load_config()
        cfg.setdefault('services', {})
        cfg['services'][sid] = {'enabled': _bool(get, 'enabled')}
        core.save_config(cfg)
        return _ok('Saved settings for %s' % sid)

    def set_pause(self, get):
        cfg = core.load_config()
        paused = _bool(get, 'paused')
        cfg['watch_paused'] = paused
        core.save_config(cfg)
        core.add_event('-', 'monitoring_paused' if paused else 'monitoring_resumed',
                       'All checks temporarily paused' if paused else 'Monitoring resumed')
        return _ok('Monitoring paused' if paused else 'Monitoring resumed',
                   data={'watch_paused': paused})

    def get_config(self, get):
        cfg = core.load_config()
        smtp = dict(cfg['smtp'])
        has_password = bool(smtp.pop('password', ''))
        data = {k: v for k, v in cfg.items() if k not in ('smtp', 'projects', 'services')}
        data.update(smtp=smtp, has_password=has_password, service=core.daemon_status())
        return _ok(data=data)

    def save_email_settings(self, get):
        cfg = core.load_config()
        security = _str(get, 'security', 'ssl')
        if security not in ('ssl', 'starttls', 'none'):
            return _err('Invalid encryption type')
        from_email = _str(get, 'from_email')
        if from_email and not core.EMAIL_RE.match(from_email):
            return _err('Invalid "From email" address')
        default_email = _str(get, 'default_email')
        bad = core.invalid_emails(default_email)
        if bad:
            return _err('Invalid default email: %s' % ', '.join(bad))

        cfg['smtp'].update(
            host=_str(get, 'host'),
            port=_int(get, 'port', 465, 1, 65535),
            security=security,
            username=_str(get, 'username'),
            from_email=from_email,
            from_name=_str(get, 'from_name') or 'aaPanel Node Watchdog',
        )
        # The password travels base64-encoded so the panel's input filtering cannot alter special characters.
        password = core.b64decode_text(_str(get, 'password_b64'))
        if password:
            cfg['smtp']['password'] = password
        cfg['default_email'] = default_email
        cfg['cc_default'] = _bool(get, 'cc_default')
        core.save_config(cfg)
        return _ok('Email settings saved')

    def save_settings(self, get):
        cfg = core.load_config()
        cfg.update(
            interval=_int(get, 'interval', 30, 10, 3600),
            confirm_delay=_int(get, 'confirm_delay', 5, 0, 60),
            log_lines=_int(get, 'log_lines', 60, 0, 500),
            repeat_minutes=_int(get, 'repeat_minutes', 0, 0, 10080),
            notify_recovery=_bool(get, 'notify_recovery'),
            notify_manual_stop=_bool(get, 'notify_manual_stop'),
            notify_restart=_bool(get, 'notify_restart'),
        )
        core.save_config(cfg)
        return _ok('Settings saved')

    def save_project(self, get):
        name = _str(get, 'project')
        if not name or name not in core.list_node_projects():
            return _err('Project not found')
        email = _str(get, 'email')
        bad = core.invalid_emails(email)
        if bad:
            return _err('Invalid email: %s' % ', '.join(bad))
        cfg = core.load_config()
        cfg['projects'][name] = {
            'enabled': _bool(get, 'enabled'),
            'email': ', '.join(core.parse_emails(email)),
            'log_file': _str(get, 'log_file'),
        }
        core.save_config(cfg)
        return _ok('Saved settings for %s' % name)

    def test_email(self, get):
        cfg = core.load_config()
        recipients = core.parse_emails(_str(get, 'to') or cfg.get('default_email'))
        if not recipients:
            return _err('Enter a recipient or set the default email first')
        subject, text, html_body = core.build_email('crash', 'example-app', [
            ('Note', 'If you can read this, SMTP is configured correctly.'),
        ], test=True)
        try:
            core.send_mail(cfg['smtp'], recipients, subject, text, html_body)
        except Exception as e:
            return _err(core.friendly_smtp_error(e))
        core.add_event('-', 'email_sent', 'test -> %s' % ', '.join(recipients))
        return _ok('Test email sent to %s' % ', '.join(recipients))

    def test_project_alert(self, get):
        name = _str(get, 'project')
        project = core.list_node_projects().get(name)
        if not project:
            return _err('Project not found')
        cfg = core.load_config()
        recipients = core.recipients_for(cfg, name)
        if not recipients:
            return _err('No recipient: set a default email or an email for this project')

        state = core.read_json(core.STATE_FILE, {})
        prev = dict((state.get('projects') or {}).get(name) or {}) if isinstance(state, dict) else {}
        st = core.probe_project(name, core.ProcSnapshot())
        if st['running']:
            prev.update(pid=st['pid'], pids=st['pids'], rss_kb=st['rss_kb'], started_at=st['started_at'],
                        last_seen=time.time(), peak_rss_kb=max(prev.get('peak_rss_kb') or 0, st['rss_kb']))
        details = core.Watchdog.crash_details(cfg, name, project, prev, time.time())
        subject, text, html_body = core.build_email('crash', name, details['rows'], details['log_file'],
                                                    details['log_tail'], test=True)
        try:
            core.send_mail(cfg['smtp'], recipients, subject, text, html_body)
        except Exception as e:
            return _err(core.friendly_smtp_error(e))
        core.add_event(name, 'email_sent', 'test alert -> %s' % ', '.join(recipients))
        return _ok('Test alert for %s sent to %s' % (name, ', '.join(recipients)))

    def get_events(self, get):
        return _ok(data=core.read_events(_int(get, 'limit', 300, 1, 2000)))

    def clear_events(self, get):
        try:
            open(core.EVENTS_FILE, 'w').close()
        except (IOError, OSError) as e:
            return _err(str(e))
        return _ok('Event log cleared')

    def service_control(self, get):
        ok, msg = core.control_daemon(_str(get, 'op'))
        return _ok(msg) if ok else _err(msg)
