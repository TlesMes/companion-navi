# 다음 세션 복원 절차 — 나비야 본학습 (GPU 재개 후)

> 현황(2026-07-02): generate로 **양성·음성 4400개 완료**(Drive 보존). backgrounds/RIRs만
> 미생성(경로 함정, 아래 해결됨). 남은 순서: background generate → augment → train → export → eval.
> 학습 산출물·데이터는 전부 `MyDrive/navi_ko/`에 있음 — GPU 시간 손실 없이 이어감.

## 왜 매번 이걸 다시 하나
Colab VM은 휘발 — `/content`(코드·config·설치물)는 런타임 초기화 때 다 날아간다. **Drive만 영속**.
그래서 새 VM마다 ①설치 ②마운트+링크 ③config 재작성이 필수. 데이터·GPU 산출물은 Drive에 누적됨.

## 순서 (셀 단위)

### 1. 설치 (새 VM 필수)
```python
!apt-get -qq install -y espeak-ng libsndfile1 ffmpeg sox portaudio19-dev
!pip install -q "livekit-wakeword[train,eval,export,voxcpm]"
```

### 2. Drive 마운트 + output 링크
```python
from google.colab import drive
drive.mount('/content/drive')
!rm -rf /content/output
!ln -sfn /content/drive/MyDrive/navi_ko/output /content/output
!ls -ld /content/output   # → /content/output -> /content/drive/.../output 한 줄이면 OK
```

### 3. config 재작성 (★ 경로 3개 전부 Drive — 함정 주의)
```python
import os; os.makedirs('configs', exist_ok=True)
```
```yaml
%%writefile configs/navi_ko.yaml
model_name: navi_ko
data_dir: /content/drive/MyDrive/navi_ko/data
target_phrases:
  - "나비야"
tts_backend: voxcpm
n_samples: 2000
n_samples_val: 200
model:
  model_type: conv_attention
  model_size: small
steps: 50000
augmentation:
  clip_duration: 2.0
  background_paths:
    - /content/drive/MyDrive/navi_ko/data/backgrounds
  rir_paths:
    - /content/drive/MyDrive/navi_ko/data/rirs
```

> **함정(2026-07-02에 당함):** `background_paths`·`rir_paths`는 `data_dir`를 안 따라간다.
> 별도 필드이고 기본값이 `./data/backgrounds`·`./data/rirs`(로컬 상대경로)라, 명시 안 하면
> Drive 데이터를 못 찾아 "No background noise files found, skipping"으로 조용히 건너뛴다.
> → background 클래스 없이 학습되면 소음 오탐↑. 위처럼 Drive 절대경로로 반드시 명시.

### 4. setup 생략 — 데이터 이미 Drive에 있음
VoxCPM(4.7G)·ACAV(17G)·backgrounds(555M·wav 770개)·RIRs 전부 `MyDrive/navi_ko/data`에 보존됨.
`setup` 다시 돌릴 필요 없음. (확인만: `!du -sh /content/drive/MyDrive/navi_ko/data/*`)

### 5. generate — background 240개만 채움 (양성·음성 4400은 resume 스킵)
```python
!livekit-wakeword generate configs/navi_ko.yaml > generate.log 2>&1
!grep -i "background\|skipping\|complete" generate.log
```
- **정상:** "Generated 200 ... background_train" / "Generated 40 ... background_test"
- **실패(또 skipping):** config의 `background_paths` 경로·wav 존재 재확인

### 6. augment → train → export → eval (로그 파일 출력)
```python
!livekit-wakeword augment configs/navi_ko.yaml > augment.log 2>&1; tail -3 augment.log
!livekit-wakeword train   configs/navi_ko.yaml > train.log   2>&1; tail -12 train.log
!livekit-wakeword export  configs/navi_ko.yaml > export.log  2>&1; tail -3 export.log
!livekit-wakeword eval    configs/navi_ko.yaml > eval.log    2>&1; tail -12 eval.log
```
터미널 모니터: `tail -f /content/train.log` (50000스텝이라 김).

### 7. eval 판정 + ONNX 확보
- eval의 **Recall / FPPH**가 이번 본학습의 답. (스모크 땐 20개라 Recall 0%였음 — 무의미)
- 산출물: `MyDrive/navi_ko/output/navi_ko/navi_ko.onnx` (**livekit-format** — 우리 openWakeWord
  어댑터 직결 아님. 통합은 livekit 런타임용 어댑터 별도 or 정확도 보고 결정).

## 한도 걸리면
generate·train 다 **resume 지원**(split별 existing 카운트 / train은 체크포인트).
Drive에 누적되니 끊겨도 같은 순서로 재개하면 만든 데까지 이어감. VM만 새로 뜨면 1~3 다시.

## 미해결 / 실측 대기
- **한국어 정확도** — VoxCPM2 발음이 "약간 동남아 톤"(비원어민). 합성-vs-합성 eval은 낙관 편향 →
  **진짜 판정은 원어민 마이크 실측**(Track A `owww_mic.py`). 미달 시 레버 = your-voice 주입(녹음 10개
  보유) 또는 원어민 한국어 TTS. 상세 → `docs/research/d7_wakeword_ko.md` §6.
- **conv_attention → 어댑터 직결 안 됨**(livekit 자체 런타임용). 통합 시 신규 어댑터 or dnn+tflite 재검토.
