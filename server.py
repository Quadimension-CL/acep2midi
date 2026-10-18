#!/usr/bin/env python3
"""
acep2midi 的本地网页服务：无授权、无外部依赖。

    python3 server.py            # http://127.0.0.1:8850/
    ACE_PORT=9000 python3 server.py

接口：
    GET  /                网页界面
    GET  /api/health      健康检查
    POST /api/convert     multipart 上传 file=<.acep>，可选 bpm / filename
"""

from __future__ import annotations

import errno
import json
import mimetypes
import os
import re
import sys


from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from acep2midi import AcepError, load_bytes, project_to_midi  # noqa: E402

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"
HOST = os.environ.get("ACE_HOST", os.environ.get("HOST", "127.0.0.1"))
# 同时接受 PORT（预览器/容器通用）与 ACE_PORT（本工具专用），后者优先
PORT = int(os.environ.get("ACE_PORT") or os.environ.get("PORT") or "8850")
MAX_UPLOAD = int(os.environ.get("ACE_MAX_UPLOAD", str(64 * 1024 * 1024)))


def parse_multipart(raw: bytes, content_type: str) -> dict[str, tuple[str, bytes]]:
    """极简 multipart/form-data 解析，返回 {字段名: (文件名, 内容)}。"""
    match = re.search(r'boundary="?([^";]+)"?', content_type or "")
    if not match or not content_type.lower().startswith("multipart/form-data"):
        return {}
    boundary = b"--" + match.group(1).encode("latin-1")
    fields: dict[str, tuple[str, bytes]] = {}
    for part in raw.split(boundary):
        if b"\r\n\r\n" not in part:
            continue
        head, _, body = part.partition(b"\r\n\r\n")
        name_match = re.search(rb'name="([^"]*)"', head)
        if not name_match:
            continue
        name = name_match.group(1).decode("utf-8", "ignore")
        file_match = re.search(rb'filename="([^"]*)"', head)
        filename = file_match.group(1).decode("utf-8", "ignore") if file_match else ""
        if body.endswith(b"\r\n"):
            body = body[:-2]
        fields[name] = (filename, body)
    return fields


def safe_midi_name(source_name: str, requested: str = "") -> str:
    base = requested.strip() or source_name
    base = base.replace("\\", "/").split("/")[-1]
    base = re.sub(r'[\x00-\x1f<>:"|?*]', "", base).strip()
    base = re.sub(r"\.(acep|zip)$", "", base, flags=re.I)
    if not base:
        base = "ace_project"
    if not base.lower().endswith(".mid"):
        base += ".mid"
    return base[:120]


def content_disposition(filename: str) -> str:
    ascii_name = re.sub(r"[^A-Za-z0-9._-]", "_", filename) or "output.mid"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename, safe='')}"


class Handler(BaseHTTPRequestHandler):
    server_version = "acep2midi/1.0"
    protocol_version = "HTTP/1.1"

    # ---------- 基础 ----------
    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data: dict, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def log_message(self, fmt, *args):
        sys.stderr.write("  %s\n" % (fmt % args))

    # ---------- 路由 ----------
    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        if path in ("/", "/index.html"):
            return self._serve(PUBLIC / "index.html")
        if path == "/api/health":
            return self._json({"ok": True, "service": "acep2midi"})
        if path == "/favicon.ico":
            return self._send(204, b"", "image/x-icon")
        # public/ 下的其它静态文件（示例工程等）
        target = (PUBLIC / path.lstrip("/")).resolve()
        if str(target).startswith(str(PUBLIC.resolve())) and target.is_file():
            return self._serve(target)
        return self._send(404, b"404 Not Found", "text/plain; charset=utf-8")

    do_HEAD = do_GET

    def do_POST(self):
        path = unquote(urlparse(self.path).path)
        if path == "/api/convert":
            return self.api_convert()
        return self._json({"success": False, "message": "Endpoint not found"}, 404)

    def _serve(self, target: Path):
        if not target.is_file():
            return self._send(404, b"404 Not Found", "text/plain; charset=utf-8")
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/"):
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype)

    # ---------- 转换 ----------
    def api_convert(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return self._json({"success": False, "message": "No file received"}, 400)
        if length > MAX_UPLOAD:
            return self._json({"success": False,
                               "message": f"File too large (limit {MAX_UPLOAD // 1024 // 1024} MB)"}, 413)

        raw = self.rfile.read(length)
        fields = parse_multipart(raw, self.headers.get("Content-Type") or "")
        upload = fields.get("file")
        if not upload or not upload[1]:
            return self._json({"success": False, "message": "Missing the file field"}, 400)

        source_name, payload = upload
        requested = fields.get("filename", ("", b""))[1].decode("utf-8", "ignore")
        bpm_raw = fields.get("bpm", ("", b""))[1].decode("utf-8", "ignore")

        try:
            _documents, project = load_bytes(payload, source=source_name or "<upload>")
        except AcepError as exc:
            return self._json({"success": False, "message": str(exc)}, 422)

        if bpm_raw:
            try:
                bpm = float(bpm_raw)
                if 20 <= bpm <= 400:
                    project.bpm = bpm
            except ValueError:
                pass

        try:
            midi = project_to_midi(project, title=Path(source_name).stem or "acep2midi")
        except AcepError as exc:
            return self._json({"success": False, "message": str(exc)}, 422)

        filename = safe_midi_name(source_name, requested)
        seconds = project.duration_ticks / (project.ppq * project.bpm / 60.0)
        names = "|".join(t.name.replace("|", "/") for t in project.tracks)[:900]
        # 响应头只能是 latin-1，中文轨道名按 RFC 5987 编码，前端 decodeURIComponent 还原
        names_ascii = quote(names, safe="|")
        counts = "|".join(str(len(t)) for t in project.tracks)

        self._send(200, midi, "audio/midi", {
            "Content-Disposition": content_disposition(filename),
            "X-MIDI-Notes": str(project.note_count),
            "X-MIDI-Tracks": str(len(project.tracks)),
            "X-MIDI-BPM": f"{project.bpm:g}",
            "X-MIDI-PPQ": str(project.ppq),
            "X-MIDI-Duration": f"{seconds:.2f}",
            "X-MIDI-Track-Names": names_ascii,
            "X-MIDI-Track-Notes": counts,
        })


def main():
    ThreadingHTTPServer.allow_reuse_address = True
    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as exc:
        if exc.errno in (errno.EADDRINUSE, errno.EACCES):
            print(f"Port {PORT} is already in use. Free it, or start on another port:", file=sys.stderr)
            print(f"  lsof -nP -iTCP:{PORT} -sTCP:LISTEN", file=sys.stderr)
            print(f"  ACE_PORT=8851 python3 server.py", file=sys.stderr)
            raise SystemExit(1)
        raise
    print(f"ACEP -> MIDI is running at http://{HOST}:{PORT}/")
    print("Press Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
