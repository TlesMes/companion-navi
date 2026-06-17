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

## 다음 판단 포인트

- **청크 스트리밍 TTFA 측정** — `get_tts_wav` 첫 청크까지 시간. RTF 1.4여도 TTFA가 짧으면 체감 실시간성 충분할 수 있음. GPU 결정보다 먼저 확인할 가치 있음.
- **GPU 가속** — CPU RTF/TTFA가 부족하다고 판정나면 착수. Windows 네이티브 DirectML은 `onnxruntime-directml` 경로(ONNX export 필요, 별도 과제).
