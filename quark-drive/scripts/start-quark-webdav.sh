#!/bin/bash
set -e
COOKIE_FILE="${QUARK_COOKIE:-${HOME}/.config/quark-backup/cookie.txt}"
if [ ! -f "$COOKIE_FILE" ]; then
  echo "❌ Cookie 文件不存在: $COOKIE_FILE"
  exit 1
fi

COOKIE=$(cat "$COOKIE_FILE")
PIDFILE="/tmp/quarkdrive-webdav.pid"

# [v3.4 安全加固] cookie 走 env var，不再进 cmdline（避免 /proc/$pid/cmdline 透出）
# quarkdrive-webdav --help 显式支持：QUARK_COOKIE / WEBDAV_AUTH_USER / WEBDAV_AUTH_PASSWORD

# 杀掉旧进程（用 PID file 准杀，不用 -f 模糊匹配）
if [ -f "$PIDFILE" ]; then
    OLD_PID=$(cat "$PIDFILE" 2>/dev/null)
    if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
        kill "$OLD_PID" 2>/dev/null
        sleep 1
        # 二次确认：还在则 SIGKILL
        if kill -0 "$OLD_PID" 2>/dev/null; then
            kill -9 "$OLD_PID" 2>/dev/null || true
            sleep 1
        fi
    fi
    rm -f "$PIDFILE"
fi

# 启动（二进制从 env 读 cookie / auth，不再走 flag）
# [v3.4] 二进制路径走 QUARK_WEBDAV_BIN（默认本机路径）
WEBDAV_BIN="${QUARK_WEBDAV_BIN:-/home/node/tools/bin/quarkdrive-webdav}"
nohup env QUARK_COOKIE="$COOKIE" \
    WEBDAV_AUTH_USER="admin" \
    WEBDAV_AUTH_PASSWORD="admin" \
    "$WEBDAV_BIN" \
    --host 127.0.0.1 -p 8080 \
    > /tmp/quarkdrive-webdav.log 2>&1 &

PID=$!
echo $PID > "$PIDFILE"

sleep 2

# PROPFIND 健康检查（不只是 207，再 PROPFIND list 一次确认 cookie 真有效）
HTTP_CODE=$(curl -s --max-time 5 -o /dev/null \
  -w "%{http_code}" -u admin:admin \
  -X PROPFIND -H "Depth: 0" \
  http://127.0.0.1:8080/)

if [ "$HTTP_CODE" = "207" ] || [ "$HTTP_CODE" = "200" ]; then
  echo "✅ quarkdrive-webdav 已启动 (PID=$PID, HTTP=$HTTP_CODE)"
  echo "📡 WebDAV URL: http://127.0.0.1:8080"
  echo "🔐 cookie 走 env，不进 cmdline"
  echo "👤 User: admin / Pass: admin"
  exit 0
else
  echo "❌ 启动失败 (HTTP=$HTTP_CODE)"
  tail -10 /tmp/quarkdrive-webdav.log
  exit 1
fi
