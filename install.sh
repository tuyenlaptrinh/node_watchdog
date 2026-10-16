#!/bin/bash
PATH=/bin:/sbin:/usr/bin:/usr/sbin:/usr/local/bin:/usr/local/sbin:~/bin
export PATH

install_tmp='/tmp/bt_install.pl'
plugin_path=/www/server/panel/plugin/node_watchdog
service_name=node_watchdog
service_file=/etc/systemd/system/${service_name}.service

python_bin=/www/server/panel/pyenv/bin/python3
[ -x "$python_bin" ] || python_bin=$(command -v python3)

Install_node_watchdog()
{
    echo 'Installing Node.js Watchdog...' > $install_tmp
    mkdir -p $plugin_path
    if [ -f "$(dirname "$0")/node_watchdog_main.py" ] && [ "$(cd "$(dirname "$0")" && pwd)" != "$plugin_path" ]; then
        \cp -a -r "$(dirname "$0")"/* $plugin_path/
    fi
    chmod 600 $plugin_path/*.json 2>/dev/null
    chmod 700 $plugin_path/*.py $plugin_path/install.sh 2>/dev/null

    if [ -d /run/systemd/system ]; then
        cat > $service_file <<EOF
[Unit]
Description=aaPanel Node.js Watchdog
After=network-online.target

[Service]
Type=simple
ExecStart=${python_bin} ${plugin_path}/node_watchdog_daemon.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
        systemctl daemon-reload
        systemctl enable ${service_name} >/dev/null 2>&1
        systemctl restart ${service_name}
    else
        (crontab -l 2>/dev/null | grep -v node_watchdog_daemon; \
         echo "@reboot ${python_bin} ${plugin_path}/node_watchdog_daemon.py >/dev/null 2>&1 &") | crontab -
        pkill -f node_watchdog_daemon.py >/dev/null 2>&1
        nohup ${python_bin} ${plugin_path}/node_watchdog_daemon.py >/dev/null 2>&1 &
    fi
    echo 'Successify' > $install_tmp
}

Uninstall_node_watchdog()
{
    if [ -f $service_file ]; then
        systemctl stop ${service_name} >/dev/null 2>&1
        systemctl disable ${service_name} >/dev/null 2>&1
        rm -f $service_file
        systemctl daemon-reload
    fi
    (crontab -l 2>/dev/null | grep -v node_watchdog_daemon) | crontab - 2>/dev/null
    pkill -f node_watchdog_daemon.py >/dev/null 2>&1
    rm -rf $plugin_path
}

if [ "${1}" == 'install' ]; then
    Install_node_watchdog
elif [ "${1}" == 'uninstall' ]; then
    Uninstall_node_watchdog
else
    echo "Usage: bash install.sh install|uninstall"
fi
