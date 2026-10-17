#!/bin/sh
# 入口脚本：支持用 PUID / PGID 指定运行身份，保证容器进程与刮削软件
# （Jellyfin / Emby 等）使用同一个用户 / 组，从而对媒体库有写权限。
set -e

if [ -n "$PUID" ] && [ -n "$PGID" ]; then
    if ! getent group "$PGID" >/dev/null 2>&1; then
        groupadd -g "$PGID" mediatool
    fi
    GROUP_NAME="$(getent group "$PGID" | cut -d: -f1)"

    if ! getent passwd "$PUID" >/dev/null 2>&1; then
        useradd -u "$PUID" -g "$PGID" -M -s /bin/sh mediatool
    fi
    USER_NAME="$(getent passwd "$PUID" | cut -d: -f1)"

    mkdir -p /app/configs /app/logs
    chown -R "$PUID:$PGID" /app/configs /app/logs

    echo "[entrypoint] 以 $USER_NAME (uid=$PUID gid=$PGID, group=$GROUP_NAME) 身份启动"
    exec gosu "$USER_NAME" "$@"
fi

echo "[entrypoint] 未设置 PUID/PGID，以当前用户（$(id -u):$(id -g)）启动"
exec "$@"
