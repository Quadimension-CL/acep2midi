#!/bin/sh
# 启动 ACEP → MIDI 网页界面
cd "$(dirname "$0")" || exit 1
exec python3 server.py
