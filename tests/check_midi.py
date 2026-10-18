#!/usr/bin/env python3
"""Verify that generated files are well-formed Standard MIDI Files.

Used by CI as a cross-platform check (a heredoc in the workflow would break
on Windows runners, which use PowerShell).

    python tests/check_midi.py out-demo.mid out-realistic.mid
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path


def check(path: Path) -> list[str]:
    problems: list[str] = []
    data = path.read_bytes()

    if data[:4] != b"MThd":
        return [f"{path}: missing MThd header"]
    if len(data) < 14:
        return [f"{path}: header truncated"]

    _length, fmt, declared, division = struct.unpack(">IHHH", data[4:14])
    if fmt not in (0, 1, 2):
        problems.append(f"{path}: unexpected format {fmt}")
    if division & 0x8000:
        problems.append(f"{path}: SMPTE time division is not supported")
    elif division == 0:
        problems.append(f"{path}: PPQ is zero")

    offset = 14
    parsed = 0
    note_on = note_off = 0
    while offset < len(data):
        if data[offset:offset + 4] != b"MTrk":
            problems.append(f"{path}: expected MTrk at offset {offset}")
            break
        size = struct.unpack(">I", data[offset + 4:offset + 8])[0]
        body = data[offset + 8:offset + 8 + size]
        if len(body) != size:
            problems.append(f"{path}: track {parsed} truncated")
            break
        parsed += 1
        on, off, ended = _scan_track(body, path, parsed - 1, problems)
        note_on += on
        note_off += off
        if not ended:
            problems.append(f"{path}: track {parsed - 1} has no End of Track")
        offset += 8 + size

    if offset != len(data):
        problems.append(f"{path}: {len(data) - offset} trailing byte(s)")
    if parsed != declared:
        problems.append(f"{path}: header declares {declared} tracks, found {parsed}")
    if note_on != note_off:
        problems.append(f"{path}: {note_on} note-on vs {note_off} note-off")

    return problems


def _read_vlq(body: bytes, index: int) -> tuple[int, int]:
    value = 0
    while True:
        byte = body[index]
        index += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, index


def _scan_track(body: bytes, path: Path, index: int, problems: list[str]):
    """Walk one MTrk, counting note on/off events and finding End of Track."""
    position = 0
    note_on = note_off = 0
    while position < len(body):
        _delta, position = _read_vlq(body, position)
        if position >= len(body):
            break
        status = body[position]

        if status == 0xFF:                          # meta event
            if position + 1 >= len(body):
                break
            meta_type = body[position + 1]
            position += 2
            length, position = _read_vlq(body, position)
            position += length
            if meta_type == 0x2F:
                return note_on, note_off, True
        elif status in (0xF0, 0xF7):                # sysex
            position += 1
            length, position = _read_vlq(body, position)
            position += length
        elif status & 0x80:                         # channel voice
            kind = status & 0xF0
            if kind in (0x80, 0x90):
                if kind == 0x90 and position + 2 < len(body) and body[position + 2] > 0:
                    note_on += 1
                else:
                    note_off += 1
                position += 3
            elif kind in (0xC0, 0xD0):
                position += 2
            else:
                position += 3
        else:
            problems.append(f"{path}: invalid status byte in track {index}")
            break
    return note_on, note_off, False


def main(argv: list[str]) -> int:
    paths = [Path(arg) for arg in argv[1:]]
    if not paths:
        print("usage: check_midi.py FILE [FILE ...]", file=sys.stderr)
        return 2

    failures = 0
    for path in paths:
        if not path.is_file():
            print(f"FAIL {path}: not found")
            failures += 1
            continue
        problems = check(path)
        if problems:
            failures += 1
            for problem in problems:
                print(f"FAIL {problem}")
        else:
            print(f"ok   {path} ({path.stat().st_size} bytes)")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
