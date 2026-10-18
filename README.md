# acep2midi

Convert ACE Studio `.acep` project files into standard MIDI — **no license code, no network, no third-party dependencies.**

Two ways to use it: batch conversion from the command line, or drag-and-drop in a local web UI.

[简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [한국어](README.ko.md) · [Русский](README.ru.md)

---

## Features

- **Offline** — everything runs on your machine; files never leave it.
- **Zero dependencies** — Python standard library only.
- **Two containers supported** — modern ACE Studio 1.9+ (zstd + base64) and older zip-based variants.
- **Lyrics included** — syllable text is written into MIDI `Lyric` meta events.
- **Format 1 MIDI** — one track per vocal track, with tempo, time signature and track names.
- **Library API** — import it and drive it from your own scripts.

## Requirements

- Python 3.9 or newer (3.14+ recommended — it ships a built-in zstd decoder)
- For zstd-compressed projects on older Pythons: install `zstandard` or `pyzstd`, or have the `zstd` CLI on your `PATH`

## Command line

```bash
# Convert one file; writes song.mid next to it
python3 acep2midi.py song.acep

# Choose output path and override BPM
python3 acep2midi.py song.acep -o out.mid --bpm 96

# Inspect what was parsed without writing a file
python3 acep2midi.py song.acep --inspect

# Batch convert
for f in *.acep; do python3 acep2midi.py "$f"; done

# Run the built-in self-test
python3 acep2midi.py --selftest
```

All options:

| Flag | Description |
| --- | --- |
| `-o, --output PATH` | Output `.mid` path (default: same name as input) |
| `--bpm BPM` | Override the tempo stored in the project |
| `--ppq N` | Override the auto-detected ticks-per-quarter-note |
| `--title TEXT` | Title written into the MIDI file |
| `--no-lyrics` | Do not write lyric meta events |
| `--skip-muted` | Skip muted tracks |
| `--inspect` | Print parse results only |
| `--selftest` | Run built-in tests |

Example `--inspect` output:

```
File      samples/demo-realistic.acep
Container zip
Documents 3
BPM       112
PPQ       480
Time sig  4/4
Duration  about 2.1 s (0.04 min)
Tracks    3, 7 notes in total

  1. Lead                           3 notes  range 69–76    0.0s →   2.1s
  2. Harmony                        2 notes  range 57–60    0.0s →   2.1s
  3. Drum                           2 notes  range 36–38    0.0s →   0.4s
```

> The CLI prints these labels in Chinese. Set `ACE_LANG=en` for English output.

## Web UI

```bash
python3 server.py
```

Open <http://127.0.0.1:8850/>, drop in an `.acep` file, and download the `.mid`.
You can optionally override the BPM and the output filename. The result panel shows
track count, note count, duration, and per-track statistics.

Change the port with an environment variable:

```bash
ACE_PORT=9000 python3 server.py
```

## As a library

```python
from acep2midi import load_acep, project_to_midi

project = load_acep("song.acep")
print(project.bpm, project.ppq, len(project.tracks), project.note_count)

midi_bytes = project_to_midi(project, title="My Song")
open("out.mid", "wb").write(midi_bytes)
```

Main objects:

- `Project` — `tracks`, `bpm`, `ppq`, `time_signature`, `note_count`, `duration_ticks`
- `Track` — `name`, `notes`, `muted`, `kind`
- `Note` — `start`, `dur`, `pitch`, `vel`, `lyric` (times in ticks)
- Errors are raised as `AcepError`

## How it parses `.acep`

Two container layouts are supported by the same logic:

| Version | Structure |
| --- | --- |
| ACE Studio 1.9+ (e.g. 1.9.6) | Outer JSON; the `content` field is **zstd-compressed, then base64-encoded** |
| Older / community variants | Zip container holding one or more JSON documents |

Parsing steps:

1. **Unpack** — zstd via the stdlib `compression.zstd`, `zstandard`, `pyzstd`, or the system `zstd` CLI (first available wins); gzip/zlib also supported. Otherwise treated as zip or raw JSON. Text encodings tried in order: UTF-8 BOM, UTF-8, UTF-16.
2. **Find notes** — walk every JSON document recursively and treat an array of objects as a note list when at least 60% of the sample has both a pitch field and a start-time field. Once found, stop descending into that array and take the track name from the parent object (`name` / `trackName` / `channelName` / `title`).
3. **Normalise field names** — comparison ignores case, underscores and hyphens, with several candidates per concept:

   | Meaning | Candidate fields |
   | --- | --- |
   | Pitch | `pitch` `midiNote` `noteNumber` `key` `note` |
   | Start | `start` `startTick` `startTime` `onset` `tick` `time` `offset` `pos` |
   | Duration | `duration` `dur` `length` `len` `noteLength` |
   | Velocity | `velocity` `vel` `volume` `gain` `intensity` `loudness` |
   | Lyric | `lyric` `lyrics` `word` `syllable` `text` |
   | BPM | `bpm` `tempo` `beatsPerMinute` |
   | PPQ | `ppq` `ticksPerBeat` `ticksPerQuarter` `resolution` `division` |

4. **Pitch** — accepts numbers (`60`) and note names (`C4`, `F#3`, `Bb5`).
5. **Velocity** — accepts 0–127 integers and 0–1 normalised floats.
6. **Time unit** — prefer the pattern's `timeUnit` (`tick` / `sec`). Otherwise infer: fractional starts/durations at a small magnitude, or all starts below 64, mean seconds and are converted with `PPQ × BPM / 60`; everything else is treated as ticks.
7. **PPQ** — project files usually omit the resolution, so it is inferred from the tick grid: take the GCD of all starts, durations and adjacent gaps to get the grid step, try `step × 2/× 4/× 8/× 16`, then score candidates by how many notes land on bar lines and beats. ACE 1.9.6 projects measure as **480** (matching the VSQX they export).
8. **Output** — format 1 MIDI. Track 0 carries the title, tempo and time signature; each note track gets its own MTrk, lyrics become `Lyric` meta events, and MIDI channel 10 (percussion) is skipped.

If a version uses entirely different field names, add them to the
`PITCH_KEYS` / `START_KEYS` / `DUR_KEYS` / `VEL_KEYS` / `LYRIC_KEYS` tuples at the top of
`acep2midi.py` — nothing else needs to change.

### Verified against a real project

A real ACE Studio 1.9.6 project (four vocal tracks) was cross-checked against the VSQX export
saved alongside it:

- All 4 tracks, 597 notes (including 47 sustain notes) and 464 syllables were extracted.
- Syllable-by-syllable comparison against the VSQX showed a constant position offset (40200 ticks)
  and matching durations, confirming the time axis is parsed correctly.
- The inferred PPQ of 480 and BPM of 190 match the VSQX's `<resolution>480</resolution>` and
  `<tempo>190</tempo>`.

## Project layout

```
acep2midi/
├── acep2midi.py      conversion core + CLI
├── server.py         local web server (stdlib only)
├── public/index.html web UI
├── samples/          test .acep files
└── README.md
```

## Limitations

- **Notes and lyrics only.** Pitch bend, vibrato, breathiness, energy and other automation
  (the `parameters` section) are not written to MIDI; every track uses the default piano patch,
  which you can reassign in your DAW.
- **Audio tracks are skipped.** `type: "audio"` tracks reference an external WAV; the project
  file does not contain the audio itself.
- **Heuristic, not an official format.** If a real project parses incorrectly, run `--inspect`
  first to see which tracks and notes were detected, then extend the candidate field lists above.
- **PPQ is inferred.** In rare cases (every note on a whole beat) the guess can be wrong;
  use `--ppq` to set it manually.
- Output is format 1 (multi-track) MIDI.

## License

MIT
