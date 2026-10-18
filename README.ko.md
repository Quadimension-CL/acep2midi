# acep2midi

ACE Studio의 `.acep` 프로젝트 파일을 표준 MIDI로 변환합니다. **라이선스 코드 없음, 네트워크 없음, 서드파티 의존성 없음.**

명령줄에서 일괄 변환하거나, 로컬 웹 UI에 끌어다 놓는 두 가지 방식으로 사용할 수 있습니다.

[English](README.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [Русский](README.ru.md)

---

## 특징

- **완전 오프라인** — 모든 처리가 로컬에서 이루어지며 파일이 외부로 전송되지 않습니다.
- **의존성 없음** — Python 표준 라이브러리만 사용합니다.
- **두 가지 컨테이너 지원** — 최신 ACE Studio 1.9+(zstd + base64)와 구버전 zip 형식.
- **가사 유지** — 음절 텍스트를 MIDI `Lyric` 메타 이벤트로 기록합니다.
- **format 1 MIDI** — 보컬 트랙마다 하나의 트랙, 템포·박자·트랙 이름 포함.
- **라이브러리로 사용 가능** — 자체 스크립트에서 import 할 수 있습니다.

## 요구 사항

- Python 3.9 이상 (3.14+ 권장 — zstd 디코더 내장)
- 구버전 Python에서 zstd 압축 프로젝트를 다룰 때: `zstandard` 또는 `pyzstd` 설치, 또는 `PATH`에 `zstd` 명령 준비

## 명령줄

```bash
# 파일 하나 변환 (같은 위치에 같은 이름의 .mid 생성)
python3 acep2midi.py song.acep

# 출력 경로와 BPM 지정
python3 acep2midi.py song.acep -o out.mid --bpm 96

# 파싱 결과만 확인 (파일을 쓰지 않음)
python3 acep2midi.py song.acep --inspect

# 일괄 변환
for f in *.acep; do python3 acep2midi.py "$f"; done

# 내장 자체 테스트
python3 acep2midi.py --selftest
```

전체 옵션:

| 옵션 | 설명 |
| --- | --- |
| `-o, --output PATH` | 출력 `.mid` 경로 (기본값: 입력과 같은 이름) |
| `--bpm BPM` | 프로젝트에 저장된 BPM 덮어쓰기 |
| `--ppq N` | 자동 추정된 PPQ(4분음표당 tick 수) 덮어쓰기 |
| `--title TEXT` | MIDI에 기록할 제목 |
| `--no-lyrics` | 가사 메타 이벤트를 기록하지 않음 |
| `--skip-muted` | 음소거된 트랙 제외 |
| `--inspect` | 파싱 결과만 출력 |
| `--selftest` | 내장 테스트 실행 |

`--inspect` 출력 예시:

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

> CLI는 기본적으로 영어로 출력됩니다. `ACE_LANG=zh`로 중국어, `ACE_LANG=en`으로 영어 고정이 가능합니다.

## 웹 UI

```bash
python3 server.py
```

<http://127.0.0.1:8850/> 을 열고 `.acep` 파일을 끌어다 놓으면 됩니다.
BPM 덮어쓰기와 출력 파일명 지정이 가능하며, 변환 후 트랙 수·음표 수·재생 시간·트랙별 통계가
표시되고 바로 `.mid`를 내려받을 수 있습니다.

UI는 브라우저 언어(중국어/영어)를 자동으로 따르며, `?lang=en` 또는 `?lang=zh`로 고정할 수 있습니다.

포트는 환경 변수로 변경할 수 있습니다:

```bash
ACE_PORT=9000 python3 server.py
```

## 라이브러리로 사용

```python
from acep2midi import load_acep, project_to_midi

project = load_acep("song.acep")
print(project.bpm, project.ppq, len(project.tracks), project.note_count)

midi_bytes = project_to_midi(project, title="My Song")
open("out.mid", "wb").write(midi_bytes)
```

주요 객체:

- `Project` — `tracks`, `bpm`, `ppq`, `time_signature`, `note_count`, `duration_ticks`
- `Track` — `name`, `notes`, `muted`, `kind`
- `Note` — `start`, `dur`, `pitch`, `vel`, `lyric` (시간 단위는 tick)
- 오류는 모두 `AcepError`

## `.acep` 파싱 방식

두 가지 컨테이너를 동일한 로직으로 처리합니다:

| 버전 | 구조 |
| --- | --- |
| ACE Studio 1.9+ (예: 1.9.6) | 바깥은 JSON. `content` 필드가 **zstd 압축 후 base64**된 데이터 |
| 구버전 / 커뮤니티 변형 | zip 컨테이너. 내부에 하나 이상의 JSON 문서 |

파싱 단계:

1. **압축 해제** — zstd를 표준 라이브러리 `compression.zstd`, `zstandard`, `pyzstd`, 시스템 `zstd` 명령 순으로 시도합니다(gzip/zlib도 지원). 그 외에는 zip 또는 원시 JSON으로 처리합니다. 인코딩은 UTF-8 BOM, UTF-8, UTF-16 순으로 시도.
2. **음표 탐색** — 모든 JSON을 재귀적으로 순회하며, 객체 배열 샘플의 60% 이상이 음높이 필드와 시작 시각 필드를 함께 가지면 음표 목록으로 판정합니다. 찾으면 해당 배열 내부로는 내려가지 않고, 부모 객체에서 트랙 이름(`name` / `trackName` / `channelName` / `title`)을 가져옵니다.
3. **필드명 정규화** — 비교 시 대소문자·밑줄·하이픈을 무시하고, 개념별로 여러 후보 이름을 유지합니다:

   | 의미 | 후보 필드 |
   | --- | --- |
   | 음높이 | `pitch` `midiNote` `noteNumber` `key` `note` |
   | 시작 | `start` `startTick` `startTime` `onset` `tick` `time` `offset` `pos` |
   | 길이 | `duration` `dur` `length` `len` `noteLength` |
   | 벨로시티 | `velocity` `vel` `volume` `gain` `intensity` `loudness` |
   | 가사 | `lyric` `lyrics` `word` `syllable` `text` |
   | BPM | `bpm` `tempo` `beatsPerMinute` |
   | PPQ | `ppq` `ticksPerBeat` `ticksPerQuarter` `resolution` `division` |

4. **음높이** — 숫자(`60`)와 음이름(`C4`, `F#3`, `Bb5`) 모두 지원.
5. **벨로시티** — 0–127 정수와 0–1 정규화 실수 지원.
6. **시간 단위** — pattern의 `timeUnit`(`tick` / `sec`)을 우선 사용. 없으면 추정합니다: 시작 위치나 길이에 소수가 있고 값이 작거나, 모든 시작 위치가 64 미만이면 초로 보고 `PPQ × BPM / 60`으로 변환합니다. 그 외에는 tick으로 처리합니다.
7. **PPQ** — 프로젝트 파일은 보통 해상도를 기록하지 않으므로 tick 그리드에서 역산합니다. 모든 시작 위치·길이·인접 간격의 최대공약수를 그리드 폭으로 삼고, `그리드 × 2/× 4/× 8/× 16`을 후보로 하여 마디선·박에 놓이는 음표 비율로 점수를 매깁니다. ACE 1.9.6 실측값은 **480**(동봉된 VSQX와 일치).
8. **출력** — format 1 MIDI. 0번 트랙에 제목·템포·박자를 기록하고, 음표 트랙마다 MTrk를 만들며, 가사는 `Lyric` 메타 이벤트로, MIDI 채널 10(퍼커션)은 회피합니다.

버전에 따라 전혀 다른 필드명을 쓴다면 `acep2midi.py` 상단의
`PITCH_KEYS` / `START_KEYS` / `DUR_KEYS` / `VEL_KEYS` / `LYRIC_KEYS`에 추가하기만 하면 되고,
다른 로직은 수정할 필요가 없습니다.

### 실제 프로젝트로 검증

실제 ACE Studio 1.9.6 프로젝트(보컬 4트랙)와 같은 폴더의 VSQX 내보내기를 대조했습니다:

- 4개 트랙, 597개 음표(연장 음표 47개 포함), 464개 음절을 모두 추출.
- VSQX와 음절 단위로 비교한 결과 위치 차이가 일정(40200 tick)하고 길이도 일치해 시간축 파싱이 올바름을 확인.
- 추정한 PPQ 480·BPM 190은 VSQX의 `<resolution>480</resolution>`, `<tempo>190</tempo>`와 일치.

## 파일 구조

```
acep2midi/
├── acep2midi.py      변환 코어 + CLI
├── server.py         로컬 웹 서버 (표준 라이브러리만)
├── public/index.html 웹 UI
├── samples/          테스트용 .acep
└── README.md
```

## 제한 사항

- **음표와 가사만** 추출합니다. 피치 벤드, 비브라토, 브레스, 에너지 등 오토메이션
  (프로젝트의 `parameters` 섹션)은 MIDI에 기록되지 않으며, 음색은 기본 피아노로 설정되므로
  DAW에서 다시 지정해야 합니다.
- **오디오 트랙은 건너뜁니다.** `type: "audio"` 트랙은 외부 WAV를 참조하며, 프로젝트 파일 자체에
  오디오 데이터는 들어 있지 않습니다.
- **휴리스틱 방식이며 공식 포맷이 아닙니다.** 실제 프로젝트에서 파싱이 이상하면 먼저 `--inspect`로
  감지된 트랙과 음표 수를 확인하고, 위 후보 필드 표에 이름을 추가하세요.
- **PPQ는 추정값**입니다. 드물게(모든 음표가 박 위에 있는 경우 등) 틀릴 수 있으므로 `--ppq`로
  직접 지정할 수 있습니다.
- 출력은 format 1(멀티트랙) MIDI입니다.

## 라이선스

MIT
