# D7 웨이크워드 — 한국어 "나비야" 모델 학습 (livekit-wakeword)

> **한 줄 요지:** 호스팅(openwakeword.com)은 한국어 베이스가 없어 막혔다 → 무료·소유·진짜
> 한국어(VoxCPM2)인 **livekit-wakeword**로 "나비야" 모델을 굽는다. **학습 엔진은 openWakeWord와
> 동일**, 출력만 우리 어댑터에 다리(tflite) 하나로 잇는다.
>
> 배경 결정: [architecture.md](../design/architecture.md) D7 · 엔진 피벗(Vosk→openWakeWord)은
> [progress.md](../progress.md) 상단 Stage 참조.

---

## 1. 왜 이 경로인가 (3개 후보 비교)

| 경로 | 한국어 | 비용 | your-voice | 우리 어댑터 | 판정 |
| :-- | :-- | :-- | :-- | :-- | :-- |
| openwakeword.com 호스팅 | ❌ **한국어 체크포인트 없음**(영어 Lessac 교차언어 임시방편) | 23,317 크레딧(보유 0)·~7.8h | ✓ 클론(유료) | ✓ onnx | **막힘** — 한국어 품질·비용 |
| 공식 `automatic_…ipynb` | ❌ piper-sample-generator 영어 전용 | 무료 | ❌ | ✓ onnx | 한국어 불가 |
| 공식 `training_models.ipynb`(수동) | △ BYO 클립이면 가능 | 무료 | ✓(폴더에 녹음) | ✓ onnx | 한국어 TTS·export를 **우리가 전부 배선** |
| **livekit-wakeword** | ✓ **VoxCPM2(진짜 다국어, 30개 언어)** | 무료 | ⚠️ 미문서화 | △ **tflite 다리 1개** | **채택** |

**핵심:** 수동 노트북에서 우리가 직접 채워야 할 빈칸(다화자 한국어 TTS + export)을 livekit이
이미 채워뒀다. livekit ≈ "이 수동 파이프라인 + VoxCPM2 + export"의 패키징. (Apache 2.0)

---

## 2. 큰 그림 — 같은 엔진, TTS만 다름

```
       ┌─ 공통 엔진(openWakeWord 학습, 언어 무관) ──────────────┐
"나비야" 클립 →│ 멜스펙+임베딩 특징 → 분류기 학습 → 모델 export │→ 우리 어댑터
       └────────────────────────────────────────────────────┘
            ↑
   언어가 묶이는 곳은 여기 하나(TTS). livekit은 이걸 VoxCPM2로 채움.
```

웨이크워드는 **사용자별 학습이 아니다** — 특정 사람이 아니라 "나비야" *구문*을 잡는 화자-독립
모델이다(시리·알렉사처럼). 그래서 **개발자가 한 번 구워 배포**하면 모든 사용자가 학습 0으로 쓴다.
your-voice 주입은 *내 인스턴스용 선택 부스트*일 뿐, 배포 모델엔 합성 다수화자가 정답.

---

## 3. ★ 이미 있는 것(livekit) vs 우리가 붙일 것(글루)

### 이미 구현됨 — 적극 사용

- **VoxCPM2 한국어 합성** — "나비야"를 30개 언어 중 한국어 다화자로 대량 생성(`tts_backend: voxcpm`)
- **데이터 증강** — 배경음·SNR 믹스·adversarial negative
- **학습** — `conv_attention`(자체 고정밀) 또는 **`dnn` head**(openWakeWord 호환) 선택
- **export** — `.onnx`(기본) / `.tflite`(openWakeWord 호환, **dnn head만**)
- CLI 일괄 실행(`setup`/`run`) 또는 스테이지(`generate`/`augment`/`train`/`export`)

### 우리가 붙일 것 — 글루 6개

1. **Colab 환경** — 시스템 deps + `pip install`(아래 4절)
2. **config.yaml 작성** — `target_phrases: ["나비야"]` + `tts_backend: voxcpm` + **dnn head**
3. **tflite export** — `export --format tflite`(openWakeWord 런타임이 먹는 형식)
4. **어댑터 한 줄** — `OpenWakeWordWakeWord`의 `inference_framework`를 인자화(현재 `"onnx"` 고정 →
   `"tflite"` 허용). [navi/ear/wakeword.py:209](../../navi/ear/wakeword.py)
5. **모델 배치** — 산출 `.tflite`를 `secrets/navi_ko.tflite`로, config `ear.wakeword.model_path` 교체
6. **실측** — `owww_mic.py --model secrets/navi_ko.tflite`로 recall 측정

> 배선의 90%는 이미 우리 쪽에 있다: 어댑터가 `model_path`(커스텀 모델)를 이미 받는다. 남은 건
> "onnx 외에 tflite도 허용" 한 줄과, config의 `model_path` 한 줄.

---

## 4. 구체 절차 (Colab, GPU 런타임)

```bash
# 4-1. 시스템 의존성
apt install -y espeak-ng libsndfile1 ffmpeg sox portaudio19-dev

# 4-2. 패키지 (voxcpm=한국어, tflite=openWakeWord 호환 export)
pip install "livekit-wakeword[train,eval,export,voxcpm,tflite]"
```

```yaml
# 4-3. configs/navi_ko.yaml
model_name: navi_ko
target_phrases:
  - "나비야"
tts_backend: voxcpm        # ← 한국어(VoxCPM2). 빼면 영어 Piper로 떨어진다
n_samples: 10000           # 다국어는 화자/운율 다양성 위해 넉넉히
model:
  model_type: dnn          # ← openWakeWord 호환 export의 전제(아래 5절). conv_attention 아님
  model_size: small
steps: 50000
```

```bash
# 4-4. 학습 + export
livekit-wakeword run configs/navi_ko.yaml          # generate→augment→train 일괄
livekit-wakeword export configs/navi_ko.yaml --format tflite
# → navi_ko.tflite 다운로드 → secrets/navi_ko.tflite
```

---

## 5. 우리 어댑터로의 다리 (제약 1개)

- **현재:** `OpenWakeWordWakeWord`가 `inference_framework="onnx"` 고정. tflite 로드하려면 이 인자를
  `__init__` 파라미터로 빼고 config에서 주입. (기본값 onnx 유지 → 기존 거동 불변)
- **제약 — dnn head 전용:** livekit의 *openWakeWord 호환 tflite export는 dnn head만 지원*한다.
  livekit의 고정밀 `conv_attention`은 **livekit 자체 런타임(WakeWordModel)용**이라 우리 openWakeWord
  런타임엔 안 맞는다. → 우리 어댑터를 재사용하려면 **config에서 dnn head로 학습**해야 한다.
- **대안(후순위):** 정확도 위해 `conv_attention`을 쓰고 싶으면, livekit `WakeWordModel`을 감싸는
  새 어댑터를 `WakeWord` 계약 뒤에 작성(엔진 교체는 어댑터 1개 + 팩토리 한 줄 — Vosk↔openWakeWord와
  동일 방식). **v1은 dnn+tflite로 어댑터 재사용 권장**, 정확도 부족 시 conv_attention+신규 어댑터.

---

## 6. 미해결 · 리스크

- **한국어 정확도(경로 무관 리스크):** livekit도 *"다국어 모델은 영어보다 정확도가 낮다"*고 명시.
  구글 오디오 임베딩의 영어 편향(arch D7 메모)을 어느 경로든 상속한다 → **실측으로만 판정**.
- **your-voice 주입:** livekit 문서에 방법 없음. 개인 부스트는 후순위 — (a) 레포 `generate` 스테이지
  조사로 클립 주입 경로 찾기, 또는 (b) 수동 openWakeWord 노트북(BYO 클립) 폴백. 이미 받아둔 내 녹음
  10개를 그때 투입. *sanity check 단계엔 합성-only로 충분*(배포 모델은 어차피 화자-독립).
- **dnn head 정확도:** conv_attention보다 낮을 수 있음 — 어댑터 재사용 vs 정확도 트레이드오프.
- **tflite 로드 검증:** livekit tflite가 실제 우리 `openwakeword.Model(inference_framework="tflite")`에
  로드되는지는 **첫 빌드 때 확인**(미검증 가정).
- **VoxCPM2 GPU 요구량:** 미명시 → Colab T4로 시도, OOM이면 `n_samples`·배치 조정.

---

## 7. 다음 작업

1. Colab에서 `navi_ko.yaml`(나비야·voxcpm·dnn) → `run` → `--format tflite` export.
2. 어댑터 `inference_framework` 인자화([wakeword.py:209](../../navi/ear/wakeword.py)) + config `model_path`.
3. `secrets/navi_ko.tflite` 배치 → `owww_mic.py`로 recall 실측(내 목소리 + 다른 사람).
4. **통과 → D7 한국어 모델 확정.** 미달 → your-voice 주입(수동 노트북 폴백) 또는 conv_attention+신규 어댑터.
