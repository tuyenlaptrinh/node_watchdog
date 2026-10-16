# coding: utf-8
"""Background loop of the Node.js Watchdog plugin. Run by systemd (node_watchdog.service).

Usage:
    python3 node_watchdog_daemon.py          run forever
    python3 node_watchdog_daemon.py --once   run a single check and exit
"""
import fcntl
import os
import signal
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import node_watchdog_core as core  # noqa: E402

_running = True


def _stop(signum, frame):
    global _running
    _running = False


def main():
    if '--once' in sys.argv:
        core.Watchdog().run_once()
        return 0

    lock = open(os.path.join(core.PLUGIN_DIR, 'daemon.lock'), 'w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, OSError):
        print('Another node_watchdog daemon is already running')
        return 0

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    with open(core.DAEMON_PID_FILE, 'w') as f:
        f.write(str(os.getpid()))
    core.add_event('-', 'daemon_started', 'PID %d' % os.getpid())

    watchdog = core.Watchdog()
    try:
        while _running:
            started = time.time()
            try:
                watchdog.run_once()
            except Exception:
                core.add_event('-', 'daemon_error', traceback.format_exc()[-1500:], 'error')
            try:
                interval = max(10, int(core.load_config().get('interval') or 30))
            except (TypeError, ValueError):
                interval = 30
            while _running and time.time() - started < interval:
                time.sleep(1)
    finally:
        core.add_event('-', 'daemon_stopped', 'PID %d' % os.getpid())
        try:
            os.remove(core.DAEMON_PID_FILE)
        except OSError:
            pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
