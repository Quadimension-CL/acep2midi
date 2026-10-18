# acep2midi

ACE Studio の `.acep` プロジェクトファイルを標準 MIDI に変換します。**ライセンスコード不要・ネットワーク不要・サードパーティ依存なし。**

コマンドラインでの一括変換と、ローカル Web UI へのドラッグ＆ドロップの 2 通りで使えます。

[English](README.md) · [简体中文](README.zh-CN.md) · [한국어](README.ko.md) · [Русский](README.ru.md)

---

## 特徴

- **完全オフライン** — 処理はすべて手元で完結し、ファイルは外部に送信されません。
- **依存ゼロ** — Python 標準ライブラリのみ。
- **2 種類のコンテナに対応** — 新しい ACE Studio 1.9+（zstd + base64）と、旧来の zip 形式。
- **歌詞を保持** — 音節テキストを MIDI の `Lyric` メタイベントに書き込みます。
- **format 1 MIDI** — ボーカルトラックごとに 1 トラック、テンポ・拍子・トラック名付き。
- **ライブラリとして利用可能** — 自作スクリプトから import できます。

## 動作環境

- Python 3.9 以上（3.14+ 推奨。zstd デコーダを標準搭載）
- 古い Python で zstd 圧縮プロジェクトを扱う場合：`zstandard` または `pyzstd` を導入するか、`PATH` に `zstd` コマンドを用意してください

## コマンドライン

```bash
# 1 ファイル変換（同名の .mid を同じ場所に出力）
python3 acep2midi.py song.acep

# 出力先と BPM を指定
python3 acep2midi.py song.acep -o out.mid --bpm 96

# 解析結果だけ確認（ファイルは書き出さない）
python3 acep2midi.py song.acep --inspect

# 一括変換
for f in *.acep; do python3 acep2midi.py "$f"; done

# 内蔵セルフテスト
python3 acep2midi.py --selftest
```

オプション一覧：

| オプション | 説明 |
| --- | --- |
| `-o, --output PATH` | 出力する `.mid` のパス（既定：入力と同名） |
| `--bpm BPM` | プロジェクト内の BPM を上書き |
| `--ppq N` | 自動判定された PPQ（4 分音符あたりの tick 数）を上書き |
| `--title TEXT` | MIDI に書き込むタイトル |
| `--no-lyrics` | 歌詞メタイベントを書き込まない |
| `--skip-muted` | ミュートされたトラックを除外 |
| `--inspect` | 解析結果の表示のみ |
| `--selftest` | 内蔵テストを実行 |

`--inspect` の出力例：

```
File      samples/demo-realistic.acep
Container zip
Documents 3
BPM       112
PPQ       480
Time sig  4/4
Duration  about 2.1 s (0.04 min)
Tracks    3 (7 notes)

  1. Lead                           3 notes  range 69–76     0.0s →    2.1s
  2. Harmony                        2 notes  range 57–60     0.0s →    2.1s
  3. Drum                           2 notes  range 36–38     0.0s →    0.4s
```

> CLI は既定で英語です。`ACE_LANG=zh` で中国語、`ACE_LANG=en` で英語に固定できます。

## Web UI

```bash
python3 server.py
```

<http://127.0.0.1:8850/> を開き、`.acep` をドラッグ＆ドロップするだけです。
BPM の上書きと出力ファイル名の指定が可能で、変換後はトラック数・音符数・再生時間・
トラックごとの音符数が表示され、そのまま `.mid` をダウンロードできます。

UI はブラウザの言語（中国語／英語）に自動追従します。`?lang=en` または `?lang=zh` で固定もできます。

ポートは環境変数で変更できます：

```bash
ACE_PORT=9000 python3 server.py
```

## ライブラリとして使う

```python
from acep2midi import load_acep, project_to_midi

project = load_acep("song.acep")
print(project.bpm, project.ppq, len(project.tracks), project.note_count)

midi_bytes = project_to_midi(project, title="My Song")
open("out.mid", "wb").write(midi_bytes)
```

主なオブジェクト：

- `Project` — `tracks`、`bpm`、`ppq`、`time_signature`、`note_count`、`duration_ticks`
- `Track` — `name`、`notes`、`muted`、`kind`
- `Note` — `start`、`dur`、`pitch`、`vel`、`lyric`（時間単位は tick）
- エラーはすべて `AcepError`

## `.acep` の解析方法

2 種類のコンテナを同じロジックで扱います：

| バージョン | 構造 |
| --- | --- |
| ACE Studio 1.9+（例：1.9.6） | 外側が JSON。`content` フィールドは **zstd 圧縮後に base64** されたデータ |
| 旧版／コミュニティ派生 | zip コンテナ。中に 1 つ以上の JSON ドキュメント |

解析手順：

1. **展開** — zstd を標準ライブラリ `compression.zstd`、`zstandard`、`pyzstd`、システムの `zstd` コマンドの順で試します（gzip/zlib にも対応）。それ以外は zip または生 JSON として扱います。文字コードは UTF-8 BOM、UTF-8、UTF-16 の順に試行。
2. **音符の探索** — すべての JSON を再帰的に走査し、オブジェクト配列のサンプルの 60% 以上が音高フィールドと開始時刻フィールドを併せ持つ場合に音符リストと判定します。見つけたらその配列の内部には降りず、親オブジェクトからトラック名（`name` / `trackName` / `channelName` / `title`）を取得します。
3. **フィールド名の正規化** — 比較時に大文字小文字・アンダースコア・ハイフンを無視し、概念ごとに複数の候補名を持ちます：

   | 意味 | 候補フィールド |
   | --- | --- |
   | 音高 | `pitch` `midiNote` `noteNumber` `key` `note` |
   | 開始 | `start` `startTick` `startTime` `onset` `tick` `time` `offset` `pos` |
   | 長さ | `duration` `dur` `length` `len` `noteLength` |
   | ベロシティ | `velocity` `vel` `volume` `gain` `intensity` `loudness` |
   | 歌詞 | `lyric` `lyrics` `word` `syllable` `text` |
   | BPM | `bpm` `tempo` `beatsPerMinute` |
   | PPQ | `ppq` `ticksPerBeat` `ticksPerQuarter` `resolution` `division` |

4. **音高** — 数値（`60`）と音名（`C4`、`F#3`、`Bb5`）の両方に対応。
5. **ベロシティ** — 0–127 の整数と 0–1 の正規化浮動小数点に対応。
6. **時間単位** — pattern の `timeUnit`（`tick` / `sec`）を優先。ない場合は推定します：開始位置や長さに小数があり値が小さい、またはすべての開始位置が 64 未満なら秒とみなし、`PPQ × BPM / 60` で変換します。それ以外は tick として扱います。
7. **PPQ** — プロジェクトファイルは通常解像度を記録しないため、tick グリッドから逆算します。すべての開始位置・長さ・隣接間隔の最大公約数をグリッド幅とし、`グリッド × 2/× 4/× 8/× 16` を候補にして、小節線・拍に乗る音符の割合で採点します。ACE 1.9.6 の実測値は **480**（同梱の VSQX と一致）。
8. **出力** — format 1 MIDI。トラック 0 にタイトル・テンポ・拍子を書き、音符トラックごとに MTrk を作成、歌詞は `Lyric` メタイベント、MIDI チャンネル 10（パーカッション）は回避します。

バージョンによってまったく異なるフィールド名を使う場合は、`acep2midi.py` 冒頭の
`PITCH_KEYS` / `START_KEYS` / `DUR_KEYS` / `VEL_KEYS` / `LYRIC_KEYS` に追加するだけで、
他のロジックは変更不要です。

### 実プロジェクトでの検証

実際の ACE Studio 1.9.6 プロジェクト（ボーカル 4 トラック）と、同じフォルダにある VSQX 書き出しを照合しました：

- 4 トラック、597 音符（うち延長音符 47）、464 音節をすべて抽出。
- VSQX と音節単位で比較したところ、位置の差は一定（40200 tick）で長さも一致し、時間軸の解析が正しいことを確認。
- 推定した PPQ 480・BPM 190 は、VSQX の `<resolution>480</resolution>` と `<tempo>190</tempo>` に一致。

## ファイル構成

```
acep2midi/
├── acep2midi.py      変換コア + CLI
├── server.py         ローカル Web サーバ（標準ライブラリのみ）
├── public/index.html Web UI
├── samples/          テスト用 .acep
└── README.md
```

## 制限事項

- **音符と歌詞のみ**。ピッチベンド、ビブラート、ブレス、エネルギーなどのオートメーション
  （プロジェクトの `parameters` セクション）は MIDI に書き込まれません。音色は既定のピアノで、
  DAW 側で割り当て直してください。
- **オーディオトラックは除外**。`type: "audio"` のトラックは外部 WAV を参照しており、
  プロジェクトファイル自体に音声データは含まれていません。
- **ヒューリスティックであり公式フォーマットではありません**。実プロジェクトで解析がおかしい場合は、
  まず `--inspect` で検出されたトラックと音符数を確認し、上記の候補フィールド表に追記してください。
- **PPQ は推定値**。ごく稀に（すべての音符が拍上にある場合など）誤る可能性があるため、
  `--ppq` で手動指定できます。
- 出力は format 1（マルチトラック）MIDI です。

## ライセンス

MIT
