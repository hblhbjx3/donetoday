#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DoneToday 同步小服务器
- 电脑 / NAS 直接运行:  python sync_server.py  (或 python3)
- Docker 运行: 镜像 python:3-alpine, 命令 python /app/sync_server.py, 挂载 /data
- 功能: 静态页面 + dayplan.json 读写 + 每次保存自动快照(保留50份) + 快照列表/恢复
- 手机/电脑浏览器访问  http://<本机IP>:8000/index.html
"""
import http.server
import socket
import os
import re
import json
import time
import threading
from urllib.parse import unquote

PORT = int(os.environ.get("DAYPLAN_PORT", "8000"))
ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(ROOT, "dayplan.json")
SNAP_DIR = os.environ.get("DAYPLAN_SNAP_DIR", os.path.join(ROOT, "snapshots"))
MAX_SNAPS = 50
LOCK = threading.Lock()


def save_snapshot(user="default"):
    src = DATA_FILE if user == "default" else user_file(user + ".json")
    if not os.path.exists(src):
        return
    os.makedirs(SNAP_DIR, exist_ok=True)
    name = "snap_%s_%s.json" % (user, time.strftime("%Y%m%d_%H%M%S", time.localtime()))
    dst = os.path.join(SNAP_DIR, name)
    with open(src, "rb") as f, open(dst, "wb") as g:
        g.write(f.read())
    snaps = sorted(
        f for f in os.listdir(SNAP_DIR) if f.startswith("snap_") and f.endswith(".json")
    )
    for old in snaps[:-MAX_SNAPS]:
        try:
            os.remove(os.path.join(SNAP_DIR, old))
        except OSError:
            pass


USERS_DIR = os.path.join(ROOT, "users")
USER_RE = re.compile(r"^[^/\\]+\.json$")


def user_file(name):
    fname = os.path.basename(unquote(name))
    if not USER_RE.match(fname):
        return None
    return os.path.join(USERS_DIR, fname)


def valid_json(raw):
    try:
        j = json.loads(raw.decode("utf-8"))
        return isinstance(j, dict) and "tasks" in j
    except Exception:
        return False


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def log_message(self, fmt, *args):
        pass  # 静默访问日志

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, PUT, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-store")

    def end_headers(self):
        self._cors()
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def _send_json(self, body, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/dayplan.json":
            with LOCK:
                if os.path.exists(DATA_FILE):
                    with open(DATA_FILE, "rb") as f:
                        body = f.read()
                    self._send_json(body)
                    return
                self.send_response(404)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(b'{"error":"no data yet"}')
                return
        m = re.match(r"^/users/([^/]+\.json)$", path)
        if m:
            fp = user_file(m.group(1))
            with LOCK:
                if fp and os.path.exists(fp):
                    with open(fp, "rb") as f:
                        body = f.read()
                    self._send_json(body)
                    return
                self.send_response(404)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(b'{"error":"no such user"}')
                return
        if path == "/api/users":
            os.makedirs(USERS_DIR, exist_ok=True)
            users = []
            with LOCK:
                for f in sorted(os.listdir(USERS_DIR)):
                    if f.endswith(".json"):
                        fp = os.path.join(USERS_DIR, f)
                        try:
                            size = os.path.getsize(fp)
                            emoji = ""
                            with open(fp, "rb") as fh:
                                data = json.loads(fh.read().decode("utf-8"))
                                emoji = (data.get("settings") or {}).get("emoji") or ""
                            users.append({"name": f[:-5], "size": size, "emoji": emoji})
                        except OSError:
                            pass
                        except Exception:
                            users.append({"name": f[:-5]})
            self._send_json(json.dumps(users).encode("utf-8"))
            return
        if path == "/api/snapshots":
            os.makedirs(SNAP_DIR, exist_ok=True)
            snaps = sorted(
                (f for f in os.listdir(SNAP_DIR)
                 if f.startswith("snap_") and f.endswith(".json")),
                reverse=True,
            )
            self._send_json(json.dumps(snaps).encode("utf-8"))
            return
        m = re.match(r"^/api/snapshots/([^/]+)$", path)
        if m:
            name = m.group(1)
            if not re.match(r"^snap_.+_\d{8}_\d{6}\.json$", name):
                self.send_response(400)
                self.end_headers()
                return
            fp = os.path.join(SNAP_DIR, name)
            if not os.path.exists(fp):
                self.send_response(404)
                self.end_headers()
                return
            with open(fp, "rb") as f:
                body = f.read()
            self._send_json(body)
            return
        super().do_GET()

    def do_DELETE(self):
        path = self.path.split("?")[0]
        m = re.match(r"^/users/([^/]+\.json)$", path)
        if m:
            fp = user_file(m.group(1))
            with LOCK:
                if fp and os.path.exists(fp):
                    os.remove(fp)
                    self.send_response(204)
                    self.end_headers()
                    return
                self.send_response(404)
                self.end_headers()
                return
        self.send_response(405)
        self.end_headers()

    def do_PUT(self):
        path = self.path.split("?")[0]
        target = None
        user = None
        if path == "/dayplan.json":
            target = DATA_FILE
        else:
            m = re.match(r"^/users/([^/]+\.json)$", path)
            if m:
                user = unquote(m.group(1))[:-5]
                fp = user_file(m.group(1))
                if not fp:
                    self.send_response(400)
                    self.end_headers()
                    return
                target = fp
        if target is None:
            self.send_response(405)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        if not valid_json(raw):
            self.send_response(400)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(b'{"error":"invalid json"}')
            return
        with LOCK:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as f:
                f.write(raw)
            save_snapshot(user or "default")
        self.send_response(204)
        self.end_headers()


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


if __name__ == "__main__":
    srv = Server(("0.0.0.0", PORT), Handler)
    ip = lan_ip()
    print("=" * 46)
    print(" DoneToday 同步服务已启动")
    print(" 本机访问:   http://127.0.0.1:%d/index.html" % PORT)
    print(" 手机/平板:  http://%s:%d/index.html" % (ip, PORT))
    print(" 数据文件:   %s" % DATA_FILE)
    print(" 快照目录:   %s (保留最近 %d 份)" % (SNAP_DIR, MAX_SNAPS))
    print(" Ctrl+C 停止")
    print("=" * 46)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
