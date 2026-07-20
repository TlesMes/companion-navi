# 웨이크워드 모델 — "나비야"

`navi_ko.onnx` (164,989 B) — 한국어 호출어 "나비야" 감지 모델. **커밋 자산이다.**

## 왜 여기 있나 (secrets/ 아님)

웨이크워드는 부속이 아니라 **제품 정체성**이다("하이 빅스비"와 같은 위상) — 클론한
사람이 "나비야"를 못 쓰면 이 제품이 아니다. 그리고 이 모델이 없으면 `--wakeword` 기동은
데몬이 뜨는 게 아니라 **죽는다**([navi/daemon.py](../../navi/daemon.py) `config.wakeword.ready`
검사). 자체 학습물이라 라이선스 제약이 없고 161 KB로 작아 커밋 자산으로 둔다.

`secrets/`에는 여전히 **재배포 불가·개인 자산**만 둔다(Vosk 모델, Porcupine `.ppn`/`.pv`,
fine-tune 가중치 등) — config.yaml의 해당 경로는 그대로 `secrets/`를 가리킨다.

## 출처

- **학습:** [livekit-wakeword](https://github.com/livekit/wakeword) — 학습 데이터는 VoxCPM2로
  합성한 "나비야" 발화 + 배경음·RIR 증강. 실녹음 수집 없이 만들었다.
- **구조:** `conv_attention` — 별도 다리(dnn head·tflite 변환) 없이 기존 openWakeWord
  어댑터([navi/ear/wakeword.py](../../navi/ear/wakeword.py) `OpenWakeWordWakeWord`)가 그대로 로드한다.
- **검증:** 원어민(개발자) 마이크 실측 통과 — 임계 0.5에서 점수 0.51~0.70대로 다회 감지.
  상세 경위 → [docs/research/d7_wakeword_ko.md](../../docs/research/d7_wakeword_ko.md) §8,
  [docs/progress.md](../../docs/progress.md) Stage 6·7.
- **재학습:** [notebooks/navi_ko_wakeword_train.ipynb](../../notebooks/navi_ko_wakeword_train.ipynb)
  (이어서 돌릴 땐 `notebooks/RESUME_next_session.md`).

## 설정

```yaml
# config.yaml
ear:
  wakeword:
    engine: openwakeword
    openwakeword:
      model_path: assets/wakeword/navi_ko.onnx
      threshold: 0.5    # 높을수록 엄격(오수락↓·누락↑)
```

경로는 **리포 루트 기준 상대경로**로 적는다 — 로더가 절대경로로 풀어둔다.
임계값 튜닝은 후순위(D7).

## ⚠ 첫 실행엔 네트워크가 한 번 필요하다

openWakeWord는 이 커스텀 모델 외에 **특징모델**(melspectrogram·embedding)을 쓰는데, 그건
여기 번들하지 않았다(타사 모델이라 재배포 라이선스를 피했다). 첫 실행 시
`openwakeword.utils.download_models()`가 1회 받아 venv 안에 캐시하고, 이후에는 파일이 있으면
즉시 통과해 **오프라인에서도 안전**하다.
