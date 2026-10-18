# acep2midi

把 ACE Studio 的 `.acep` 工程文件转成标准 MIDI。**无授权码、无网络、无第三方依赖。**

两种用法：命令行批量转，或本地网页拖拽转。

[English](README.md) · [日本語](README.ja.md) · [한국어](README.ko.md) · [Русский](README.ru.md)

---

## 特性

- **完全离线** —— 所有处理都在本机完成，文件不会上传。
- **零依赖** —— 只用 Python 标准库。
- **支持两种容器** —— 新版 ACE Studio 1.9+（zstd + base64）和旧版 zip 变体。
- **保留歌词** —— 音节文本写入 MIDI 的 `Lyric` 元事件。
- **format 1 MIDI** —— 每条人声轨一条音轨，带速度、拍号和轨道名。
- **可作为库调用** —— 在自己的脚本里直接 import。

## 环境要求

- Python 3.9 或更新版本（推荐 3.14+，自带 zstd 解码器）
- 旧版 Python 处理 zstd 压缩的工程时：安装 `zstandard` 或 `pyzstd`，或确保 `PATH` 里有 `zstd` 命令

## 命令行

```bash
# 转一个文件，输出到同目录同名 .mid
python3 acep2midi.py song.acep

# 指定输出、覆盖 BPM
python3 acep2midi.py song.acep -o out.mid --bpm 96

# 只看解析结果，不写文件
python3 acep2midi.py song.acep --inspect

# 批量
for f in *.acep; do python3 acep2midi.py "$f"; done

# 自检
python3 acep2midi.py --selftest
```

全部参数：

| 参数 | 说明 |
| --- | --- |
| `-o, --output PATH` | 输出的 `.mid` 路径（默认与输入同名） |
| `--bpm BPM` | 覆盖工程里的 BPM |
| `--ppq N` | 覆盖自动推断的 PPQ（每四分音符 tick 数） |
| `--title TEXT` | 写入 MIDI 的标题 |
| `--no-lyrics` | 不写入歌词元事件 |
| `--skip-muted` | 跳过静音轨道 |
| `--inspect` | 只显示解析结果，不写文件 |
| `--selftest` | 运行内置自检 |

`--inspect` 输出示例：

```
文件        samples/demo-realistic.acep
容器        zip
JSON 文档   3
BPM       112
PPQ       480
拍号        4/4
时长        约 2.1 秒（0.04 分钟）
轨道        3 条（7 个音符）

  1. 主唱                               3 音符  音域 69–76     0.0s →    2.1s
  2. 和声                               2 音符  音域 57–60     0.0s →    2.1s
  3. Drum                             2 音符  音域 36–38     0.0s →    0.4s
```

> 命令行默认英文。设 `ACE_LANG=zh` 切换中文，`ACE_LANG=en` 强制英文。

## 网页界面

```bash
python3 server.py
```

打开 <http://127.0.0.1:8850/>，拖入 `.acep` 即可。可选覆盖 BPM、自定义输出文件名；
转换完成后直接下载 `.mid`，并显示轨道数、音符数、时长与每条轨道的音符统计。

界面会自动跟随浏览器语言（中文 / 英文），也可用 `?lang=en` 或 `?lang=zh` 强制指定。

端口可用环境变量改：`ACE_PORT=9000 python3 server.py`。

## 作为库调用

```python
from acep2midi import load_acep, project_to_midi

project = load_acep("song.acep")
print(project.bpm, project.ppq, len(project.tracks), project.note_count)

midi_bytes = project_to_midi(project, title="My Song")
open("out.mid", "wb").write(midi_bytes)
```

主要对象：

- `Project`：`tracks`、`bpm`、`ppq`、`time_signature`、`note_count`、`duration_ticks`
- `Track`：`name`、`notes`、`muted`、`kind`
- `Note`：`start`、`dur`、`pitch`、`vel`、`lyric`（时间单位为 tick）
- 异常统一为 `AcepError`

## 它是怎么解析的

`.acep` 有两种容器，同一套逻辑都支持：

| 版本 | 结构 |
| --- | --- |
| ACE Studio 1.9+（如 1.9.6） | 外层 JSON，`content` 字段是 **zstd 压缩后再 base64** 的数据 |
| 旧版 / 社区变体 | zip 容器，内含一个或多个 JSON 文档 |

解析步骤：

1. **解包**：识别 zstd（标准库 `compression.zstd`、`zstandard`、`pyzstd`、系统 `zstd` 任选其一），
   也支持 gzip/zlib；否则按 zip 或裸 JSON 处理。编码依次尝试 UTF-8 BOM、UTF-8、UTF-16。
2. **找音符**：递归遍历所有 JSON，把「对象数组」判定为音符列表——要求样本中至少 60%
   的元素同时具备音高字段和起始时间字段。找到后不再深入该数组，并沿父级对象取轨道名
   （`name` / `trackName` / `channelName` / `title`）。
3. **字段归一化**：字段名比较时忽略大小写、下划线和连字符，并维护多组候选名：

   | 含义 | 候选字段 |
   | --- | --- |
   | 音高 | `pitch` `midiNote` `noteNumber` `key` `note` |
   | 起始 | `start` `startTick` `startTime` `onset` `tick` `time` `offset` `pos` |
   | 时长 | `duration` `dur` `length` `len` `noteLength` |
   | 力度 | `velocity` `vel` `volume` `gain` `intensity` `loudness` |
   | 歌词 | `lyric` `lyrics` `word` `syllable` `text` |
   | BPM | `bpm` `tempo` `beatsPerMinute` |
   | PPQ | `ppq` `ticksPerBeat` `ticksPerQuarter` `resolution` `division` |

4. **音高**：支持数字（60）和音名（`C4`、`F#3`、`Bb5`）。
5. **力度**：支持 0–127 整数与 0–1 归一化浮点。
6. **时间单位**：优先读 pattern 上的 `timeUnit`（`tick` / `sec`）；没有就推断——起点或时长带小数
   且量级不大，或所有起点都小于 64，判定为秒，按 `PPQ × BPM / 60` 换算；否则按 tick 直接使用。
7. **PPQ**：工程文件通常不写分辨率，从 tick 网格反推：对所有起点、时值和相邻间距取最大公约数
   得到网格步长，候选 `步长×2/×4/×8/×16`，再用「音符落在小节线/拍点上的比例」打分选优。
   实测 ACE 1.9.6 的工程是 **480**（与它导出的 VSQX 一致）。
8. **输出**：format 1 MIDI，0 号轨写入标题、速度、拍号；每条音符轨一个 MTrk，
   歌词写入 `Lyric` 元事件，跳过 MIDI 通道 10（打击乐）。

如果某个版本用了完全不同的字段名，在 `acep2midi.py` 顶部的
`PITCH_KEYS` / `START_KEYS` / `DUR_KEYS` / `VEL_KEYS` / `LYRIC_KEYS` 里补上即可，其余逻辑无需改动。

### 用真实工程验证过

拿一份 ACE Studio 1.9.6 的工程（4 条人声轨）和它同目录的 VSQX 导出做了交叉比对：

- 4 条轨道、597 个音符（含 47 个延音符）、464 个音节全部提取；
- 与 VSQX 逐个音节比对，音节位置差为恒定值（40200 tick），时值一致，说明时间轴解析正确；
- 推断出的 PPQ 480、BPM 190 与 VSQX 的 `<resolution>480</resolution>`、`<tempo>190</tempo>` 吻合。

## 文件结构

```
acep2midi/
├── acep2midi.py      转换核心 + 命令行
├── server.py         本地网页服务（仅标准库）
├── public/index.html 网页界面
├── samples/          两个测试用 .acep
└── README.md
```

## 已知限制

- **只提取音符与歌词**。弯音、颤音、气息、能量等自动化参数（工程里的 `parameters` 段）
  不会写入 MIDI；音色统一为默认钢琴，需要的话可在 DAW 里重新指定。
- **音频轨会被跳过**。`type: "audio"` 的轨道引用的是外部 wav，工程里不含音频数据本身。
- **启发式识别不是官方格式**。若真实工程解析结果异常，先用 `--inspect` 看识别出的轨道和音符数，
  再对照上面的候选字段表补充键名。
- **工程不记录 PPQ**，靠网格反推。极端情况下（所有音符都是整拍）可能选错，可用 `--ppq` 手动指定。
- 生成的 MIDI 是 format 1（多轨）。

## 许可证

MIT
