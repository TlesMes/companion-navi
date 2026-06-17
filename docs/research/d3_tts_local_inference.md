# GPT-SoVITS 로컬 CPU 추론 — 환경 설정 + 파이프라인 연결 메모

> 목적: Colab에서 학습한 ckpt를 WSL 로컬에서 추론. RTF 측정 + 실제 Mouth 어댑터 구현 참고용.
> 실행 기록: 2026.06.17, WSL Ubuntu 24.04 / Python 3.12 / 12코어 CPU / `research/d3-gptsovits` 브랜치.
> 코드 참조: `scripts/try/try_clone.py` — `_infer_gptsovits` 함수.

---

## 측정 RTF

| 조건 | 합성 시간 | 오디오 길이 | RTF |
|---|---|---|---|
| 첫 문장 (프로세스 시작 후) | ~25s | ~2.7s | — (셋업 비용 포함) |
| **웜 상태** | ~7s | ~5s | **~1.4** |

첫 문장 페널티(~25s)는 **프로세스당 1회** — 모델 로드 + numba JIT 컴파일. 데몬 상주 시 시작 1회 비용이므로 운영에서는 웜 RTF 1.4만 의미 있음.

---

## 환경 설정 (재현 시)

### 빠르게 넘어갈 것
- venv 생성, torch CPU 설치, `requirements.cpu.txt`, 베이스 모델(cnhubert·roberta) 다운로드 — 매끄럽게 됨.

### 막힐 뻔한 곳

**1. ffmpeg/torchcodec 없이 설치**
- `requirements.cpu.txt`는 torchcodec을 명시하지 않지만, torchaudio가 내부적으로 끌어옴.
- sudo 비번이 필요한 환경에서 apt로 ffmpeg 설치가 안 되면 → `torchaudio.load`를 soundfile로 monkeypatch(아래 참조). OGG도 soundfile이 직접 읽어서 ffmpeg 자체가 불필요해짐.

**2. `fast_langdetect` 캐시 디렉터리**
- 디렉터리가 없으면 다운로드 시도조차 안 하고 `FileNotFoundError`로 죽음.
- 첫 추론 전에 미리 생성해야 함:
  ```bash
  mkdir -p ~/GPT-SoVITS/GPT_SoVITS/pretrained_models/fast_langdetect
  ```
- 첫 추론 시 fasttext `lid.176.bin`(125MB) + open_jtalk 사전(23MB) 자동 다운로드됨 — 이후엔 캐시 사용.

**3. WSL 한글 경로**
- 프로젝트가 `/mnt/c/.../반려 ai 어플리케이션` — 한글 CWD에서 `getcwd`·`for`루프 변수가 빈 값 되는 이상 동작.
- **해결:** ASCII 심링크로 우회. `ln -sfn /mnt/c/Users/.../Projects/*/ ~/proj`
- 이후 항상 `~/proj/...` 사용. 실행 CWD는 `~/GPT-SoVITS`(ASCII) 유지.

---

## 파이프라인 연결 시 중요한 요소

### 1. `inference_webui` import = 무거운 부작용
import 순간 **모듈 레벨에서 즉시 실행**:
- `change_gpt_weights(gpt_path)` → GPT 모델 로드
- `AutoTokenizer` + `AutoModelForMaskedLM` → bert 로드
- cnhubert 경로 설정

**함의:** 데몬에서 import = 모델 로드. 매 발화마다 하면 안 됨 — 프로세스 시작 시 1회, 이후 상주.

### 2. import 전 env로 절대경로 못박기 (필수)
CWD가 repo 밖이면 기본 상대경로(`GPT_SoVITS/pretrained_models/...`)가 안 풀려 import 실패.
```python
os.environ["cnhubert_base_path"] = "/abs/path/to/GPT_SoVITS/pretrained_models/chinese-hubert-base"
os.environ["bert_path"]          = "/abs/path/to/GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large"
os.environ["gpt_path"]           = "/abs/path/to/arisu-e15.ckpt"
os.environ["sovits_path"]        = "/abs/path/to/arisu_e8_s352.pth"
```

### 3. sys.path 두 개 필요
```python
sys.path.insert(0, repo_path)                               # from GPT_SoVITS.x, import config
sys.path.insert(0, os.path.join(repo_path, "GPT_SoVITS"))  # 내부 from text.x, from feature_extractor
```
하나만 넣으면 `No module named 'text'`.

### 4. `torchaudio.load` → soundfile 패치 (import 전에)
```python
import torch as _torch
import torchaudio as _ta
import soundfile as _sf

def _sf_load(filename, *_a, **_k):
    data, sr = _sf.read(str(filename), dtype="float32", always_2d=True)
    return _torch.from_numpy(data.T), sr

_ta.load = _sf_load
# → 이 다음에 inference_webui import
```

### 5. 가중치 교체 함수의 두 얼굴
- `change_sovits_weights(path, prompt_language=, text_language=)` — **제너레이터**. `for _ in ...: pass`로 소진해야 적용. **언어 인자 필수** — 안 넘기면 마지막 yield가 미설정 변수 참조해 `UnboundLocalError`.
- `change_gpt_weights(path)` — 일반 함수, 그냥 호출.

**톤 제어 함의:** 음색 ckpt는 시작 시 1회 로드. 발화마다 바꾸는 건 `get_tts_wav`에 넘기는 **레퍼런스 wav + ref_text**뿐. 가중치 재로드 불필요.

### 6. `get_tts_wav` — 청크 yield, 스트리밍 가능
```python
for sr, audio_chunk in get_tts_wav(
    ref_wav_path=str(ref_path),
    prompt_text=ref_text,
    prompt_language=i18n("日文"),   # i18n() 통과 필수
    text=gen_text,
    text_language=i18n("日文"),
    how_to_cut=i18n("不切"),        # 문장 분할 안 함
):
    # audio_chunk: int16 ndarray. 청크 단위로 스피커에 흘리면 TTFA 단축 가능.
    pass
```
- 반환: `(sr: int, audio: np.int16 ndarray)` 튜플.
- float32로 변환: `audio.astype(np.float32) / 32768.0`
- `how_to_cut`으로 문장 분할 방식 선택 — 긴 답변은 문장 단위로 끊어 첫 문장을 먼저 내보낼 수 있음.

### 7. 언어 매핑
```python
_LANG = {"ja": "日文", "ko": "韩文", "zh": "中文", "en": "英文"}
# 사용: i18n(_LANG["ja"])
```
i18n() 없이 직접 문자열 넘기면 안 됨.

### 8. 예열 추론 (운영 팁)
데몬 부팅 후 더미 문장 1회 추론 → numba JIT 웜업 선소진. 첫 사용자 발화가 RTF 1.4로 시작됨.

---

## 청크 스트리밍 구현 시 고려 사항

RTF 1.4 비스트리밍으로는 "첫 오디오 ~1초" 원칙을 지킬 수 없어 스트리밍은 필수. `get_tts_wav`가 청크를 yield하는 구조라 파이프라인 측에서 받아 흘리면 됨.

### GPT-SoVITS 청크의 성질 (모델이 해결해 주는 것)

`get_tts_wav`가 뱉는 청크는 단순 버퍼 슬라이스가 아님. 모델 내부에서:

- **Stateful Inference** — 이전 청크 끝의 위상·상태를 기억하고, 다음 청크를 그 지점에서 이어 그림.
- **Overlap-Add / Cross-fade** — 청크 경계면을 살짝 겹쳐 부드럽게 이어붙임.

결과적으로 청크들은 **앞뒤가 완벽하게 맞물리도록 재단된 퍼즐 조각**. 백엔드의 역할은 가공이 아니라 **순서대로 유실 없이 큐에 밀어 넣는 것**만.

### 퀄리티 저하 요인 (백엔드에서 만드는 문제)

**A. 버퍼 언더런 (Buffer Underrun) — 미세한 끊김**

- 증상: 발화 중 0.1초씩 더듬거리거나 뚝뚝 끊김.
- 원인: 스피커가 현재 청크를 소비하는 속도 > CPU가 다음 청크를 생성하는 속도. 큐가 비는 순간 발생.
- 대응: 재생 시작 전 선행 버퍼링(첫 N청크를 모은 뒤 재생 시작) + 큐 모니터링.

**B. 파형 경계 노이즈 (Popping/Clicking)**

- 증상: 음성 중간에 '틱'·'팝' 잡음.
- 원인: 모델이 잘 만들어준 조각을 **백엔드에서 잘못 다룰 때** 발생. 구체적으로:
  1. **문장 단위보다 작게 텍스트를 쪼개서 각각 추론** — 모델이 '이어지는 소리'가 아닌 '독립된 소리' 두 개를 만들어 경계가 어긋남.
  2. **배열 끝 1~2 프레임 유실 / 무음 패딩 삽입** — float 변환·SR 조정 코드의 실수.
  3. **바지인(Barge-in) 시 스트림 강제 종료** — 큐를 flush 없이 프로세스를 죽이면 마지막 재생 중 잡음 발생.
- 대응: 텍스트 분할은 문장 단위(`.!?`) 이상 유지 / 배열 조작 시 프레임 수 검증 / 바지인은 스트림 drain 후 종료.

### 정리: 백엔드가 해야 할 것 / 안 해도 되는 것

| | 필요 여부 |
|---|---|
| 청크 파형 가공(fade, 제로 크로싱 정렬) | ❌ 모델이 이미 처리 |
| 순서 보장·유실 없는 큐 전달 | ✅ 필수 |
| 선행 버퍼링 (언더런 방지) | ✅ 필수 |
| 텍스트를 문장 단위 이상으로 유지 | ✅ 필수 |
| 바지인 시 스트림 안전 종료 | ✅ 필수 |

---

## 다음 판단 포인트

- **청크 스트리밍 구현** — Mouth 어댑터에서 `get_tts_wav` yield를 오디오 큐에 흘리는 것. 스트리밍은 선택이 아닌 "첫 오디오 ~1초" 원칙상 필수.
- **GPU 가속** — CPU RTF 1.4로 언더런이 지속되면 착수. Windows 네이티브 DirectML은 `onnxruntime-directml` 경로(ONNX export 필요, 별도 과제).
