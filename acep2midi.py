#!/usr/bin/env python3
"""
acep2midi —— 把 ACE Studio 的 .acep 工程文件转成标准 MIDI。

无授权、无网络、无第三方依赖。既可作为库调用，也可作为命令行工具：

    python3 acep2midi.py song.acep
    python3 acep2midi.py song.acep -o out.mid --bpm 96
    python3 acep2midi.py song.acep --inspect
    python3 acep2midi.py --selftest

支持两种容器（同一套解析逻辑）：

  1. 现代 ACE Studio（1.9+）：外层是 JSON，音符数据在 `content` 字段里，
     用 zstd 压缩后再 base64 编码。
  2. 旧版 / 社区变体：zip 容器，里面是一个或多个 JSON 文档。

不同版本字段命名不一致，所以这里不绑定固定 schema：递归扫描所有 JSON，
找出「像音符列表」的数组，再从上下文推断轨道名、BPM、PPQ 与时间单位。
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "Track", "Project", "Note", "load_acep", "load_bytes", "project_to_midi",
    "convert_file", "AcepError",
]

DEFAULT_PPQ = 480
DEFAULT_BPM = 120.0
DEFAULT_VELOCITY = 100

# 字段名候选（统一小写比较，忽略下划线）
PITCH_KEYS = ("pitch", "midinote", "midipitch", "notenumber", "notenum", "key", "note")
START_KEYS = ("start", "starttick", "starttime", "onset", "tick", "time", "begin", "offset", "pos")
DUR_KEYS = ("duration", "durationtick", "dur", "length", "len", "notelength", "durtick")
LYRIC_KEYS = ("lyric", "lyrics", "word", "syllable", "text", "phoneme")
VEL_KEYS = ("velocity", "vel", "volume", "gain", "intensity", "loudness", "dynamics")
NAME_KEYS = ("name", "trackname", "channelname", "title", "label")
BPM_KEYS = ("bpm", "tempo", "beatsperminute")
PPQ_KEYS = ("ppq", "ticksperbeat", "ticksperquarter", "ticksperquarternote",
            "resolution", "division", "tickresolution")
UNIT_KEYS = ("timeunit", "unit", "timebase", "timeformat")

# ACE Studio 的 tick 基不固定（实测 1.9.6 工程为 480），因此不再硬编码，
# 由 _infer_ppq() 从 tick 网格反推。
NOTE_NAMES = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
NOTE_RE = re.compile(r"^\s*([A-Ga-g])([#b♯♭]?)(-?\d+)\s*$")
KEY_NORM_RE = re.compile(r"[^a-z0-9]")


# --------------------------------------------------------------------------
# 多语言文案（CLI / 错误消息）
# --------------------------------------------------------------------------
MESSAGES = {
    "en": {
        "bad_zip": "Corrupt zip container: {exc}",
        "not_container": "This is neither a zip container nor valid JSON text.",
        "no_zstd": ("This is a zstd-compressed ACE project, but it cannot be decompressed here. "
                    "Upgrade to Python 3.14+, install zstandard / pyzstd, or install the zstd CLI."),
        "empty_file": "The file is empty.",
        "no_documents": "No parseable JSON documents found in the container.",
        "no_notes": "No note data found (a pitch field and a start-time field are both required).",
        "no_usable_notes": "Candidate arrays were found, but none contained usable notes.",
        "no_export_tracks": "The project has no exportable tracks; cannot build MIDI.",
        "inspect_file": "File", "inspect_container": "Container",
        "inspect_documents": "Documents", "inspect_timesig": "Time sig",
        "inspect_duration": "Duration", "inspect_tracks": "Tracks",
        "inspect_seconds": "about {sec:.1f} s ({min:.2f} min)",
        "inspect_track_line": "  {i}. {name:<28} {n:>5} notes  range {lo}–{hi}  {a:6.1f}s → {b:6.1f}s",
        "inspect_empty_track": "  {i}. {name:<28}     0 notes",
        "inspect_total": "{tracks} ({notes} notes)",
        "notes_word": "notes",
        "file_not_found": "File not found: {path}",
        "convert_failed": "Conversion failed: {exc}",
        "written": "Wrote {path}",
        "summary": "  {tracks} tracks, {notes} notes, BPM {bpm:g}, PPQ {ppq}",
        "selftest_ok": "Self-test passed.",
        "selftest_fail": "{n} check(s) failed.",
        "selftest_reject_ok": "{label}: correctly rejected",
        "selftest_reject_fail": "{label}: should have raised an error",
        "muted_suffix": " (muted)",
    },
    "zh": {
        "bad_zip": "zip 容器损坏：{exc}",
        "not_container": "既不是 zip 容器，也不是合法的 JSON 文本。",
        "no_zstd": ("这是 zstd 压缩的 ACE 工程，但当前环境解不开。"
                    "请升级到 Python 3.14+，或安装 zstandard / pyzstd，或安装 zstd 命令行工具。"),
        "empty_file": "文件是空的。",
        "no_documents": "容器里没有可解析的 JSON 文档。",
        "no_notes": "没有找到音符数据（需要同时具备音高与起始时间的字段）。",
        "no_usable_notes": "找到了候选数组，但里面没有可用的音符。",
        "no_export_tracks": "工程里没有可导出的轨道，无法生成 MIDI。",
        "inspect_file": "文件", "inspect_container": "容器",
        "inspect_documents": "JSON 文档", "inspect_timesig": "拍号",
        "inspect_duration": "时长", "inspect_tracks": "轨道",
        "inspect_seconds": "约 {sec:.1f} 秒（{min:.2f} 分钟）",
        "inspect_track_line": "  {i}. {name:<28} {n:>5} 音符  音域 {lo}–{hi}  {a:6.1f}s → {b:6.1f}s",
        "inspect_empty_track": "  {i}. {name:<28}     0 音符",
        "inspect_total": "{tracks} 条（{notes} 个音符）",
        "notes_word": "音符",
        "file_not_found": "找不到文件：{path}",
        "convert_failed": "转换失败：{exc}",
        "written": "已生成 {path}",
        "summary": "  {tracks} 条轨道，{notes} 个音符，BPM {bpm:g}，PPQ {ppq}",
        "selftest_ok": "自检通过。",
        "selftest_fail": "{n} 项失败。",
        "selftest_reject_ok": "{label}：正确拒绝",
        "selftest_reject_fail": "{label}：本应报错",
        "muted_suffix": "（静音）",
    },
}


def _detect_lang() -> str:
    """ACE_LANG=zh|en 优先，其次看系统 locale，默认英语。"""
    forced = (os.environ.get("ACE_LANG") or "").strip().lower()
    if forced.startswith("zh"):
        return "zh"
    if forced.startswith("en"):
        return "en"
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = (os.environ.get(var) or "").lower()
        if value.startswith("zh"):
            return "zh"
    return "en"


LANG = _detect_lang()


def t(key: str, **kwargs) -> str:
    text = MESSAGES.get(LANG, MESSAGES["en"]).get(key) or MESSAGES["en"].get(key, key)
    return text.format(**kwargs) if kwargs else text


class AcepError(Exception):
    """输入不是可识别的 ACEP 工程，或里面没有音符。"""


# --------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------
@dataclass
class Note:
    """一个音符。时间单位是 tick（已按 PPQ 归一化到输出用）。"""
    start: int
    dur: int
    pitch: int
    vel: int = DEFAULT_VELOCITY
    lyric: str = ""

    def as_tuple(self) -> tuple[int, int, int, int]:
        return (self.start, self.dur, self.pitch, self.vel)


@dataclass
class Track:
    name: str
    notes: list[Note] = field(default_factory=list)
    muted: bool = False
    kind: str = "sing"

    def __len__(self) -> int:
        return len(self.notes)


@dataclass
class Project:
    tracks: list[Track] = field(default_factory=list)
    bpm: float = DEFAULT_BPM
    ppq: int = DEFAULT_PPQ
    source: str = ""
    time_signature: tuple[int, int] = (4, 4)
    container: str = ""

    @property
    def note_count(self) -> int:
        return sum(len(t) for t in self.tracks)

    @property
    def duration_ticks(self) -> int:
        return max((n.start + n.dur for t in self.tracks for n in t.notes), default=0)


# --------------------------------------------------------------------------
# 字段与数值解析
# --------------------------------------------------------------------------
def _norm_key(key) -> str:
    return KEY_NORM_RE.sub("", str(key).lower())


def _lookup(obj: dict, keys) -> tuple[str | None, object]:
    """按候选名查找字段，先精确匹配，再归一化匹配。"""
    if not isinstance(obj, dict):
        return None, None
    exact = {str(k).lower(): k for k in obj}
    for key in keys:
        if key in exact:
            return exact[key], obj[exact[key]]
    normalized = {_norm_key(k): k for k in obj}
    for key in keys:
        hit = normalized.get(key)
        if hit is not None and obj[hit] is not None:
            return hit, obj[hit]
    return None, None


def parse_pitch(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(round(value))
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"-?\d+(\.\d+)?", text):
        return int(round(float(text)))
    match = NOTE_RE.match(text)
    if not match:
        return None
    letter, accidental, octave = match.groups()
    semitone = NOTE_NAMES[letter.upper()]
    semitone += {"#": 1, "♯": 1, "b": -1, "♭": -1, "": 0}[accidental]
    return (int(octave) + 1) * 12 + semitone


def parse_number(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_velocity(value) -> int:
    number = parse_number(value)
    if number is None:
        return DEFAULT_VELOCITY
    if 0 < number <= 1:              # 归一化的 0..1
        return max(1, min(127, int(round(number * 127))))
    return max(1, min(127, int(round(number))))


# --------------------------------------------------------------------------
# 遍历：找音符数组
# --------------------------------------------------------------------------
def _is_note_array(items: list) -> bool:
    if not items or not all(isinstance(x, dict) for x in items):
        return False
    sample = items[: min(12, len(items))]
    with_pitch = sum(1 for x in sample if _lookup(x, PITCH_KEYS)[0] is not None)
    with_start = sum(1 for x in sample if _lookup(x, START_KEYS)[0] is not None)
    return with_pitch >= max(1, len(sample) * 0.6) and with_start >= max(1, len(sample) * 0.6)


def _context_name(obj: dict) -> str | None:
    _key, value = _lookup(obj, NAME_KEYS)
    if isinstance(value, str) and value.strip():
        return value.strip()[:80]
    return None


def _walk(node, ctx_name, out: list[tuple[str, list[dict]]], depth=0):
    if depth > 40:
        return
    if isinstance(node, dict):
        name = _context_name(node) or ctx_name
        for key, value in node.items():
            if isinstance(value, list) and _is_note_array(value):
                out.append((name or f"Track {len(out) + 1}", value))
                continue                      # 不再深入音符数组本身
            if isinstance(value, (dict, list)):
                _walk(value, name, out, depth + 1)
    elif isinstance(node, list):
        if _is_note_array(node):
            out.append((ctx_name or f"Track {len(out) + 1}", node))
            return
        for item in node:
            _walk(item, ctx_name, out, depth + 1)


def _find_scalar(nodes, keys) -> float | None:
    """在文档里找第一个标量（BPM/PPQ 之类）。"""
    def scan(node, depth=0):
        if depth > 40:
            return None
        if isinstance(node, dict):
            _k, value = _lookup(node, keys)
            number = parse_number(value)
            if number is not None and number > 0:
                return number
            for v in node.values():
                got = scan(v, depth + 1)
                if got is not None:
                    return got
        elif isinstance(node, list):
            for v in node:
                got = scan(v, depth + 1)
                if got is not None:
                    return got
        return None

    for node in nodes:
        got = scan(node)
        if got is not None:
            return got
    return None


def _find_unit(nodes) -> str | None:
    def scan(node, depth=0):
        if depth > 40:
            return None
        if isinstance(node, dict):
            for key, value in node.items():
                if _norm_key(key) in UNIT_KEYS and isinstance(value, str):
                    text = value.strip().lower()
                    if text in ("s", "sec", "secs", "second", "seconds", "ms", "millisecond", "milliseconds"):
                        return text
                    if text in ("tick", "ticks", "ppq", "beat", "beats"):
                        return text
                got = scan(value, depth + 1)
                if got:
                    return got
        elif isinstance(node, list):
            for value in node:
                got = scan(value, depth + 1)
                if got:
                    return got
        return None

    for node in nodes:
        got = scan(node)
        if got:
            return got
    return None


# --------------------------------------------------------------------------
# 容器解包：zip / zstd+base64 / 裸 JSON
# --------------------------------------------------------------------------
def _decode_json(raw: bytes):
    for encoding in ("utf-8-sig", "utf-8", "utf-16"):
        try:
            return json.loads(raw.decode(encoding))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    return None


def _zstd_decompress(data: bytes) -> bytes | None:
    """优先用标准库（Python 3.14+），其次 zstandard/pyzstd，最后调用系统 zstd。"""
    try:
        from compression import zstd as _zstd          # Python 3.14+
        return _zstd.decompress(data)
    except Exception:
        pass
    try:
        import zstandard                                # type: ignore
        return zstandard.ZstdDecompressor().decompress(data, max_output_size=512 * 1024 * 1024)
    except Exception:
        pass
    try:
        import pyzstd                                   # type: ignore
        return pyzstd.decompress(data)
    except Exception:
        pass

    binary = shutil.which("zstd")
    if binary:
        try:
            proc = subprocess.run([binary, "-d", "-c", "-q"], input=data,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
            if proc.returncode == 0:
                return proc.stdout
        except Exception:
            pass
    return None


def _maybe_inflate(doc):
    """识别 ACE 1.9+ 的 {'compressMethod':'zstd','content':<base64>} 外层。"""
    if not isinstance(doc, dict):
        return doc
    method = str(doc.get("compressMethod") or doc.get("compress_method") or "").lower()
    payload = doc.get("content")
    if not isinstance(payload, str) or not payload:
        return doc

    raw: bytes | None = None
    if method in ("zstd", "zstandard"):
        try:
            raw = base64.b64decode(payload + "=" * (-len(payload) % 4))
        except Exception:
            return doc
        if raw[:4] == b"\x28\xb5\x2f\xfd":              # zstd magic
            out = _zstd_decompress(raw)
            if out:
                inner = _decode_json(out)
                if inner is not None:
                    return inner
    elif method in ("gzip", "zlib", "deflate"):
        import gzip
        import zlib
        try:
            raw = base64.b64decode(payload + "=" * (-len(payload) % 4))
        except Exception:
            return doc
        for opener in (gzip.decompress, zlib.decompress):
            try:
                inner = _decode_json(opener(raw))
                if inner is not None:
                    return inner
            except Exception:
                continue
    return doc


def _load_documents(raw: bytes) -> tuple[list, str]:
    """返回 (JSON 文档列表, 容器类型)。"""
    if raw[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                docs = []
                for info in zf.infolist():
                    if info.is_dir() or info.file_size > 128 * 1024 * 1024:
                        continue
                    doc = _decode_json(zf.read(info))
                    if doc is not None:
                        docs.append(_maybe_inflate(doc))
                return docs, "zip"
        except zipfile.BadZipFile as exc:
            raise AcepError(t("bad_zip", exc=exc)) from exc

    doc = _decode_json(raw)
    if doc is None:
        raise AcepError(t("not_container"))

    container = "json"
    if isinstance(doc, dict) and str(doc.get("compressMethod", "")).lower() == "zstd":
        container = "zstd"
        if _maybe_inflate(doc) is doc:
            raise AcepError(t("no_zstd"))
    return [_maybe_inflate(doc)], container


# --------------------------------------------------------------------------
# 遍历：找音符数组
# --------------------------------------------------------------------------
def _is_note_array(items: list) -> bool:
    if not items or not all(isinstance(x, dict) for x in items):
        return False
    sample = items[: min(12, len(items))]
    with_pitch = sum(1 for x in sample if _lookup(x, PITCH_KEYS)[0] is not None)
    with_start = sum(1 for x in sample if _lookup(x, START_KEYS)[0] is not None)
    return with_pitch >= max(1, len(sample) * 0.6) and with_start >= max(1, len(sample) * 0.6)


def _context_name(obj: dict) -> str | None:
    _key, value = _lookup(obj, NAME_KEYS)
    if isinstance(value, str) and value.strip():
        return value.strip()[:80]
    return None


def _walk(node, ctx_name, out: list[dict], depth=0):
    """收集音符数组，并带上所在 pattern/track 的上下文（时间单位、名字等）。"""
    if depth > 40:
        return
    if isinstance(node, dict):
        name = _context_name(node) or ctx_name
        for key, value in node.items():
            if isinstance(value, list) and _is_note_array(value):
                out.append({
                    "name": name or f"Track {len(out) + 1}",
                    "items": value,
                    "context": node,
                })
                continue                      # 不再深入音符数组本身
            if isinstance(value, (dict, list)):
                _walk(value, name, out, depth + 1)
    elif isinstance(node, list):
        if _is_note_array(node):
            out.append({"name": ctx_name or f"Track {len(out) + 1}",
                        "items": node, "context": None})
            return
        for item in node:
            _walk(item, ctx_name, out, depth + 1)


def _find_scalar(nodes, keys) -> float | None:
    """在文档里找第一个标量（BPM/PPQ 之类）。"""
    def scan(node, depth=0):
        if depth > 40:
            return None
        if isinstance(node, dict):
            _k, value = _lookup(node, keys)
            number = parse_number(value)
            if number is not None and number > 0:
                return number
            for v in node.values():
                got = scan(v, depth + 1)
                if got is not None:
                    return got
        elif isinstance(node, list):
            for v in node:
                got = scan(v, depth + 1)
                if got is not None:
                    return got
        return None

    for node in nodes:
        got = scan(node)
        if got is not None:
            return got
    return None


def _unit_of(node) -> str | None:
    """读取一个对象上的 timeUnit 字段。"""
    if not isinstance(node, dict):
        return None
    for key, value in node.items():
        if _norm_key(key) in UNIT_KEYS and isinstance(value, str):
            text = value.strip().lower()
            if text in ("s", "sec", "secs", "second", "seconds"):
                return "sec"
            if text in ("ms", "millisecond", "milliseconds"):
                return "ms"
            if text in ("tick", "ticks", "ppq", "beat", "beats"):
                return "tick"
    return None


def _find_unit(nodes) -> str | None:
    def scan(node, depth=0):
        if depth > 40:
            return None
        got = _unit_of(node)
        if got:
            return got
        if isinstance(node, dict):
            for value in node.values():
                got = scan(value, depth + 1)
                if got:
                    return got
        elif isinstance(node, list):
            for value in node:
                got = scan(value, depth + 1)
                if got:
                    return got
        return None

    for node in nodes:
        got = scan(node)
        if got:
            return got
    return None


# --------------------------------------------------------------------------
# 读取工程
# --------------------------------------------------------------------------
def load_bytes(raw: bytes, source: str = "<bytes>") -> tuple[list, Project]:
    """解析字节内容，返回 (原始 JSON 文档列表, Project)。"""
    if not raw:
        raise AcepError(t("empty_file"))

    documents, container = _load_documents(raw)
    if not documents:
        raise AcepError(t("no_documents"))

    found: list[dict] = []
    for doc in documents:
        _walk(doc, None, found)

    if not found:
        raise AcepError(t("no_notes"))

    # BPM / PPQ：工程通常不写 PPQ，需要从 tick 网格反推（见 _infer_ppq）
    bpm = _find_scalar(documents, BPM_KEYS) or DEFAULT_BPM
    ppq_raw = _find_scalar(documents, PPQ_KEYS)
    tick_based = (container == "zstd") or any(
        _unit_of(f["context"]) == "tick" for f in found if f["context"])
    if ppq_raw:
        ppq = int(ppq_raw)
    elif tick_based:
        ppq = _infer_ppq(found)
    else:
        ppq = DEFAULT_PPQ

    project = Project(bpm=bpm, ppq=ppq, source=source, container=container)
    project.time_signature = _find_time_signature(documents)

    ticks_per_second = project.ppq * project.bpm / 60.0
    used_names: dict[str, int] = {}
    for entry in found:
        items = entry["items"]
        ctx = entry["context"]
        unit = _unit_of(ctx) or _find_unit(documents)
        seconds_mode = _is_seconds(unit, items)

        name = entry["name"]
        if name.startswith("Track ") and len(items) > 0:
            name = _better_name(ctx, items, name)

        track = Track(
            name=_unique_name(name, used_names),
            muted=bool(isinstance(ctx, dict) and (ctx.get("mute") or ctx.get("muted"))),
            kind=str((ctx or {}).get("type") or "sing"),
        )
        for item in items:
            note = _parse_note(item)
            if note is None:
                continue
            start, dur = note.start, note.dur
            if seconds_mode:
                start *= ticks_per_second
                dur *= ticks_per_second
            note.start = max(0, round(start))
            note.dur = max(1, round(dur))
            track.notes.append(note)
        if track.notes:
            track.notes.sort(key=lambda n: n.start)
            project.tracks.append(track)

    if not project.tracks:
        raise AcepError(t("no_usable_notes"))

    project.ppq = max(24, min(9600, project.ppq))
    project.bpm = max(1.0, min(1000.0, project.bpm))
    return documents, project


def _infer_ppq(found) -> int:
    """
    工程里不写 PPQ 时，从 tick 网格反推。

    ACE Studio 的工程文件不记录分辨率，但音符起点/时值都是某个 tick 基的整数倍。
    典型取值：480（与 VSQX 导出一致）、960（部分版本）。判定依据：

      1. 把所有起点、时值、相邻间距取最大公约数 —— 它是网格步长的整数倍；
      2. 网格步长通常是 PPQ 的 1/4（十六分音符）或 1/8（三十二分音符）；
      3. 用拍号把小节长度算出来，选让「音符起点落在小节线上的比例」最高的候选。
    """
    values: list[int] = []
    for entry in found:
        for item in entry["items"][:4000]:
            start = parse_number(_lookup(item, START_KEYS)[1])
            dur = parse_number(_lookup(item, DUR_KEYS)[1])
            if start is not None and float(start).is_integer():
                values.append(int(start))
            if dur is not None and float(dur).is_integer() and dur > 0:
                values.append(int(dur))
        starts = sorted({int(parse_number(_lookup(i, START_KEYS)[1]))
                         for i in entry["items"][:4000]
                         if parse_number(_lookup(i, START_KEYS)[1]) is not None})
        values.extend(b - a for a, b in zip(starts, starts[1:]) if b > a)

    if not values:
        return DEFAULT_PPQ

    from math import gcd
    from functools import reduce
    grid = reduce(gcd, values)
    if grid <= 0:
        return DEFAULT_PPQ

    # 候选：网格步长 = PPQ/4 或 PPQ/8，且 PPQ 落在合理区间
    candidates = {grid * 4, grid * 8, grid * 2, grid * 16}
    candidates = {c for c in candidates if 48 <= c <= 9600}
    if not candidates:
        return DEFAULT_PPQ

    starts = [int(parse_number(_lookup(i, START_KEYS)[1]))
              for e in found for i in e["items"]
              if parse_number(_lookup(i, START_KEYS)[1]) is not None]

    def score(ppq: int) -> tuple[float, int]:
        bar = ppq * 4                      # 4/4
        on_bar = sum(1 for s in starts if s % bar == 0)
        on_beat = sum(1 for s in starts if s % ppq == 0)
        # 优先 PPQ 为 480/960 这类常见值，其次看对齐率
        common = 1 if ppq in (480, 960, 384, 768) else 0
        return (common, on_bar * 2 + on_beat, -abs(ppq - 480))

    return max(candidates, key=score)


def _better_name(ctx, items, fallback: str) -> str:
    """ACE 工程里轨道名常为空，退回用歌手信息或首句歌词命名。"""
    if isinstance(ctx, dict):
        singers = ctx.get("singers")
        if isinstance(singers, list) and singers:
            singer = singers[0].get("singer") if isinstance(singers[0], dict) else None
            if isinstance(singer, dict):
                label = singer.get("name") or singer.get("id")
                if label:
                    return f"Track {label}"
    lyrics = [n.lyric for n in (_parse_note(i) for i in items[:24]) if n and n.lyric]
    if lyrics:
        snippet = "".join(lyrics).replace("-", "")[:12]
        if snippet:
            return f"{fallback} · {snippet}"
    return fallback


def _find_time_signature(documents) -> tuple[int, int]:
    def scan(node, depth=0):
        if depth > 30:
            return None
        if isinstance(node, dict):
            num = node.get("numerator")
            den = node.get("denominator")
            if isinstance(num, int) and isinstance(den, int) and 1 <= num <= 32 and 1 <= den <= 32:
                return (num, den)
            for value in node.values():
                got = scan(value, depth + 1)
                if got:
                    return got
        elif isinstance(node, list):
            for value in node:
                got = scan(value, depth + 1)
                if got:
                    return got
        return None

    for node in documents:
        got = scan(node)
        if got:
            return got
    return (4, 4)


def _unique_name(name: str, used: dict[str, int]) -> str:
    used[name] = used.get(name, 0) + 1
    return name if used[name] == 1 else f"{name} ({used[name]})"


def _parse_note(item: dict) -> Note | None:
    _k, raw_pitch = _lookup(item, PITCH_KEYS)
    pitch = parse_pitch(raw_pitch)
    if pitch is None or not 0 <= pitch <= 127:
        return None

    _k, raw_start = _lookup(item, START_KEYS)
    start = parse_number(raw_start)
    if start is None:
        return None

    _k, raw_dur = _lookup(item, DUR_KEYS)
    dur = parse_number(raw_dur)
    if dur is None or dur <= 0:
        dur = 0.25 if isinstance(raw_start, float) else 120.0   # 缺省给一个短音符

    _k, raw_vel = _lookup(item, VEL_KEYS)
    _k, raw_lyric = _lookup(item, LYRIC_KEYS)
    lyric = ""
    if isinstance(raw_lyric, str):
        lyric = raw_lyric.strip()
        if lyric in ("-", "—", ""):          # ACE 用 "-" 表示延音
            lyric = ""

    return Note(start=start, dur=dur, pitch=pitch,
                vel=parse_velocity(raw_vel), lyric=lyric)


def _is_seconds(unit: str | None, items: list) -> bool:
    if unit == "sec":
        return True
    if unit == "ms":
        return False
    if unit == "tick":
        return False

    starts: list[float] = []
    durs: list[float] = []
    for item in items[:500]:
        s, d = parse_number(_lookup(item, START_KEYS)[1]), parse_number(_lookup(item, DUR_KEYS)[1])
        if s is not None:
            starts.append(s)
        if d is not None:
            durs.append(d)
    if not starts:
        return False

    # 明确带小数、且量级不大 → 秒
    fractional = any(not float(v).is_integer() for v in starts + durs)
    if fractional and max(starts) < 3600:
        return True
    # 全是整数但最大起点很小（不足一个 16 分音符的 tick 数）→ 秒
    if max(starts) < 64 and (not durs or max(durs) < 64):
        return True
    return False


def load_acep(path: str | Path) -> Project:
    path = Path(path)
    _docs, project = load_bytes(path.read_bytes(), source=str(path))
    return project


# --------------------------------------------------------------------------
# MIDI 输出
# --------------------------------------------------------------------------
def _vlq(value: int) -> bytes:
    value = max(0, int(value))
    out = bytearray([value & 0x7F])
    value >>= 7
    while value:
        out.insert(0, (value & 0x7F) | 0x80)
        value >>= 7
    return bytes(out)


def _meta_text(kind: int, text: str) -> bytes:
    payload = text.encode("utf-8")[:250]
    return bytes([0xFF, kind]) + _vlq(len(payload)) + payload


def _track_chunk(events: list[tuple[int, int, bytes]]) -> bytes:
    body = bytearray()
    last = 0
    for tick, order, payload in sorted(events, key=lambda e: (e[0], e[1])):
        body += _vlq(tick - last) + payload
        last = tick
    body += _vlq(0) + b"\xFF\x2F\x00"
    return b"MTrk" + struct.pack(">I", len(body)) + bytes(body)


def project_to_midi(project: Project, title: str | None = None,
                    include_lyrics: bool = True, include_muted: bool = True) -> bytes:
    tracks = [t for t in project.tracks if include_muted or not t.muted]
    if not tracks:
        raise AcepError(t("no_export_tracks"))

    ppq = project.ppq
    header = b"MThd" + struct.pack(">IHHH", 6, 1, len(tracks) + 1, ppq)

    # 0 号轨：标题 + 速度 + 拍号
    numerator, denominator = project.time_signature
    den_pow = 0
    while (2 ** den_pow) < denominator and den_pow < 8:
        den_pow += 1
    conductor: list[tuple[int, int, bytes]] = [
        (0, 0, _meta_text(0x03, title or "acep2midi")),
        (0, 0, b"\xFF\x51\x03" + int(round(60_000_000 / project.bpm)).to_bytes(3, "big")),
        (0, 0, b"\xFF\x58\x04" + bytes([numerator & 0xFF, den_pow, 24, 8])),
    ]
    chunks = [_track_chunk(conductor)]

    channel = 0
    for track in tracks:
        if channel == 9:          # 跳过打击乐通道，避免被当成鼓
            channel = 10
        label = track.name + (t("muted_suffix") if track.muted else "")
        events: list[tuple[int, int, bytes]] = [(0, 0, _meta_text(0x03, label))]
        last_lyric_tick = -1
        for note in track.notes:
            # 歌词写在音符起点（type=5 Lyric）；同一时刻只写一次
            if include_lyrics and note.lyric and note.start != last_lyric_tick:
                events.append((note.start, 0, _meta_text(0x05, note.lyric)))
                last_lyric_tick = note.start
            events.append((note.start, 1, bytes([0x90 | channel, note.pitch, note.vel])))
            events.append((note.start + note.dur, 2,
                           bytes([0x80 | channel, note.pitch, 0x40])))
        chunks.append(_track_chunk(events))
        channel = (channel + 1) % 16

    return header + b"".join(chunks)


def convert_file(src: str | Path, dst: str | Path | None = None,
                 bpm: float | None = None, title: str | None = None,
                 include_lyrics: bool = True, include_muted: bool = True,
                 ppq: int | None = None) -> tuple[Path, Project]:
    project = load_acep(src)
    if bpm:
        project.bpm = float(bpm)
    if ppq:
        project.ppq = int(ppq)
    out = Path(dst) if dst else Path(src).with_suffix(".mid")
    out.write_bytes(project_to_midi(project, title or Path(src).stem,
                                    include_lyrics=include_lyrics,
                                    include_muted=include_muted))
    return out, project


# --------------------------------------------------------------------------
# 自检
# --------------------------------------------------------------------------
def _selftest() -> int:
    def make(doc, name="t.acep"):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("project.json", json.dumps(doc, ensure_ascii=False))
        return buf.getvalue()

    cases = [
        ("tick grid / numeric pitch", make({"bpm": 120, "tracks": [
            {"name": "Lead", "notes": [
                {"pitch": 60, "start": 0, "duration": 480, "velocity": 100},
                {"pitch": 64, "start": 480, "duration": 480, "velocity": 90}]}]}), 2, 1),
        ("seconds / note names", make({"meta": {"bpm": 96}, "tracks": [
            {"name": "Vox", "notes": [
                {"pitch": "C4", "start": 0.0, "duration": 0.5},
                {"pitch": "E4", "start": 0.5, "duration": 0.5},
                {"pitch": "G4", "start": 1.0, "duration": 1.0}]}]}), 3, 1),
        ("multi-file / nested", make({"channels": [{"channelName": "Drums", "notes": [
            {"pitch": 36, "start": 0, "duration": 120},
            {"pitch": 38, "start": 240, "duration": 120}]}]}), 2, 1),
        ("normalised velocity", make({"bpm": 140, "tracks": [
            {"name": "A", "notes": [{"pitch": 72, "start": 0, "duration": 240, "velocity": 0.8}]}]}), 1, 1),
    ]

    failures = 0
    for label, blob, want_notes, want_tracks in cases:
        try:
            _docs, project = load_bytes(blob)
            midi = project_to_midi(project)
        except Exception as exc:                     # noqa: BLE001
            print(f"  FAIL {label}: {exc}")
            failures += 1
            continue
        ok = (project.note_count == want_notes and len(project.tracks) == want_tracks
              and midi[:4] == b"MThd" and midi[-3:] == b"\xFF\x2F\x00")
        print(f"  {'ok  ' if ok else 'FAIL'} {label}: "
              f"{project.note_count} notes / {len(project.tracks)} tracks / bpm {project.bpm:g}")
        failures += 0 if ok else 1

    # 错误输入应当报 AcepError
    for label, blob in [("empty file", b""), ("random bytes", b"\x00\x01\x02\x03"),
                        ("JSON without notes", json.dumps({"a": 1}).encode())]:
        try:
            load_bytes(blob)
        except AcepError:
            print("  ok   " + t("selftest_reject_ok", label=label))
        else:
            print("  FAIL " + t("selftest_reject_fail", label=label))
            failures += 1

    print("\n" + (t("selftest_ok") if not failures else t("selftest_fail", n=failures)))
    return 1 if failures else 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _inspect(path: Path) -> int:
    documents, project = load_bytes(path.read_bytes(), source=str(path))
    tps = project.ppq * project.bpm / 60.0
    seconds = project.duration_ticks / tps

    print(f"{t('inspect_file'):<10}{path}")
    print(f"{t('inspect_container'):<10}{project.container}")
    print(f"{t('inspect_documents'):<10}{len(documents)}")
    print(f"{'BPM':<10}{project.bpm:g}")
    print(f"{'PPQ':<10}{project.ppq}")
    print(f"{t('inspect_timesig'):<10}{project.time_signature[0]}/{project.time_signature[1]}")
    print(f"{t('inspect_duration'):<10}{t('inspect_seconds', sec=seconds, min=seconds / 60)}")
    print(f"{t('inspect_tracks'):<10}{t('inspect_total', tracks=len(project.tracks), notes=project.note_count)}\n")
    for i, track in enumerate(project.tracks, 1):
        if track.notes:
            lo = min(n.pitch for n in track.notes)
            hi = max(n.pitch for n in track.notes)
            first = track.notes[0].start / tps
            last = max(n.start + n.dur for n in track.notes) / tps
            print(t("inspect_track_line", i=i, name=track.name, n=len(track),
                    lo=lo, hi=hi, a=first, b=last))
        else:
            print(t("inspect_empty_track", i=i, name=track.name))
    return 0


def _ensure_utf8_stdout() -> None:
    """
    Windows 控制台默认可能是 cp1252 / cp936，直接打印中文轨道名会抛 UnicodeEncodeError。
    这里把 stdout/stderr 切成 UTF-8，并让无法编码的字符降级而不是崩溃。
    """
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        try:
            if (stream.encoding or "").lower() not in ("utf-8", "utf8"):
                stream.reconfigure(encoding="utf-8", errors="replace")
            elif stream.errors != "replace":
                stream.reconfigure(errors="replace")
        except (AttributeError, ValueError, OSError):
            # 被重定向到不支持 reconfigure 的对象时忽略，退化为 errors 处理
            pass


def main(argv=None) -> int:
    _ensure_utf8_stdout()
    parser = argparse.ArgumentParser(
        prog="acep2midi",
        description="Convert ACE Studio .acep project files to standard MIDI "
                    "(offline, no license code).",
    )
    parser.add_argument("input", nargs="?", help="input .acep file")
    parser.add_argument("-o", "--output", help="output .mid path (default: same name as input)")
    parser.add_argument("--bpm", type=float, help="override the BPM stored in the project")
    parser.add_argument("--ppq", type=int, help="override the inferred PPQ (ticks per quarter note)")
    parser.add_argument("--title", help="title written into the MIDI file")
    parser.add_argument("--no-lyrics", action="store_true", help="do not write lyric meta events")
    parser.add_argument("--skip-muted", action="store_true", help="skip muted tracks")
    parser.add_argument("--inspect", action="store_true", help="print parse results only")
    parser.add_argument("--selftest", action="store_true", help="run the built-in self-test")
    args = parser.parse_args(argv)

    if args.selftest:
        return _selftest()
    if not args.input:
        parser.print_help()
        return 2

    src = Path(args.input)
    if not src.is_file():
        print(t("file_not_found", path=src), file=sys.stderr)
        return 2

    try:
        if args.inspect:
            return _inspect(src)
        out, project = convert_file(src, args.output, args.bpm, args.title,
                                    include_lyrics=not args.no_lyrics,
                                    include_muted=not args.skip_muted,
                                    ppq=args.ppq)
    except AcepError as exc:
        print(t("convert_failed", exc=exc), file=sys.stderr)
        return 1

    print(t("written", path=out))
    print(t("summary", tracks=len(project.tracks), notes=project.note_count,
            bpm=project.bpm, ppq=project.ppq))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
