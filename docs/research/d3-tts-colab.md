# GPT-SoVITS fine-tune — Colab 가이드 (research/d3-gptsovits)

> 목적: 아리스 음성 168클립으로 GPT-SoVITS를 fine-tune해 **나비 목소리 후보**를 만들고, D3(TTS 음색) 청취 비교용 합성을 얻는다.
> 학습·청취는 Colab(무료 T4 GPU), 체크포인트는 받아와 로컬 추론. 음질은 하드웨어 무관이라 D3 결정은 Colab만으로 가능.

## ⚠️ 가장 중요: 버전은 v2 (재학습 회피)

이번 검증 우선순위는 **동일언어 합성**이다 (cross-lingual JA→KO는 후순위/생략 가능):
1. **일본어 ref → 일본어 출력** [최우선] — 아리스 fine-tune 본래 언어, 가장 깨끗
2. 한국어 ref → 한국어 출력 [보류] — 한국어 레퍼런스 음원 확보 후
3. ~~일본어 ref → 한국어 (cross-lingual)~~ [생략 가능]

당장은 JA→JA만 보지만, **나비는 한국어 퍼스트**라 KO는 취소가 아니라 보류다. GPT-SoVITS에서 **한국어(韩文)는 v2 전용**(v4는 음질↑이나 한국어 미지원)이므로, 나중에 KO를 켤 때 **재학습을 피하려면 지금 v2로 학습**해 둔다. webui 상단에서 **버전을 v2로 먼저 고정**.

> 일본어 전용으로 확정한다면 v4(48k, 음질 천장↑)로 바꿔도 된다 — 단 그때는 한국어 포기.

---

## 준비물

- `dataset/arisu.zip` (168 wav + `arisu.list`, 약 57MB) — 로컬에서 `scripts/prep/build_sovits_dataset.py`로 생성됨
- Google 계정 (Colab 무료 T4)

---

## 1. 노트북 열기 + GPU 런타임

1. https://colab.research.google.com/github/RVC-Boss/GPT-SoVITS/blob/main/Colab-WebUI.ipynb 열기
2. 상단 메뉴 **런타임 → 런타임 유형 변경 → 하드웨어 가속기 = T4 GPU** 저장

## 2. 설치 셀 실행 (약 10~15분)

- 셀 1·2 순서대로 실행. (repo clone + conda 환경 + `install.sh`로 의존성·사전학습 모델·UVR5 다운로드)
- 설치가 끝나면 **셀 3 실행 → gradio 공개 링크**(`https://xxxx.gradio.live`)가 출력된다. 이 링크를 열면 webui.

## 3. 데이터 업로드 + 경로 수정

webui를 띄운 상태에서, Colab에 **새 코드 셀**을 추가해 데이터셋을 올린다.

```python
# (1) arisu.zip 업로드
from google.colab import files
up = files.upload()                      # dataset/arisu.zip 선택

# (2) 압축 해제
import zipfile, os
os.makedirs('/content/arisu_data', exist_ok=True)
with zipfile.ZipFile('arisu.zip') as z:
    z.extractall('/content/arisu_data')  # -> /content/arisu_data/wavs/*.wav , arisu.list

# (3) .list 경로를 Colab 경로로 교정 (로컬 Windows 절대경로 → Colab 경로)
src = '/content/arisu_data/arisu.list'
wavdir = '/content/arisu_data/wavs'
out = []
for ln in open(src, encoding='utf-8'):
    if not ln.strip():
        continue
    parts = ln.rstrip('\n').split('|')
    fname = parts[0].replace('\\', '/').split('/')[-1]      # 파일명만 추출
    parts[0] = f'{wavdir}/{fname}'
    out.append('|'.join(parts))
open(src, 'w', encoding='utf-8').write('\n'.join(out) + '\n')
print(open(src, encoding='utf-8').read().splitlines()[0])   # 첫 줄 확인
print('clips:', len(out))
```

→ `.list` 경로가 `/content/arisu_data/wavs/...`로 바뀌면 OK.

## 4. 1A — 학습셋 포맷팅

webui 상단 **버전 v2 확인** 후, **1-GPT-SoVITS-TTS → 1A 학습셋 포맷팅** 탭:

| 필드 | 값 |
| :-- | :-- |
| 실험/모델 이름 (experiment name) | `arisu` |
| 텍스트 라벨 파일 (.list) | `/content/arisu_data/arisu.list` |
| 학습셋 음성 디렉터리 | `/content/arisu_data/wavs` |

- **1Aabc 원클릭("一键三连"/One-click formatting)** 실행 → 텍스트(BERT)·HuBERT·시맨틱 토큰 3단계 일괄 추출. 로그에 에러 없이 끝나는지 확인.

## 5. 1B — fine-tune 학습

**1Ba SoVITS 학습** → **1Bb GPT 학습** 순서. T4(16GB)·168클립(~14분) 권장값:

| 항목 | SoVITS | GPT | 비고 |
| :-- | :-- | :-- | :-- |
| batch size | 4~6 | 4~6 | OOM 나면 낮춤 |
| total epochs | 8~12 | 10~15 | 데이터 적어 과하면 과적합 |
| save every | 4 | 5 | 중간 ckpt 확보 |

- 데이터가 작으니 **과적합 주의** — epoch을 너무 키우면 레퍼런스 억양만 따라하고 일반화가 깨진다. 위 범위에서 시작.
- 각 단계 끝나면 `SoVITS_weights_v2/arisu_*.pth`, `GPT_weights_v2/arisu_*.ckpt`가 생성된다.

## 6. 1C — 추론·청취 (D3 핵심)

**1C 추론 → "TTS 추론 webui 열기"** 클릭 → 추론 UI에서:

1. **GPT 모델** = `arisu_*.ckpt`, **SoVITS 모델** = `arisu_*.pth` 선택
2. **레퍼런스 오디오** = 깨끗한 5~10초 클립 1개 업로드. 후보:
   - 차분: `Arisu_(Maid)_Lobby_2` / 활기: `Arisu_(Maid)_Gachaget` (둘 다 비교해볼 것 — 진행 문서에서 차분한 레퍼런스가 캐릭터성 평탄화 의심됨)
   - 레퍼런스 전사(prompt text)는 `voice_ref/Arisu_Maid/transcription.csv`에서 해당 행 복붙, **레퍼런스 언어 = 일본어**
3. **합성 텍스트 + 언어** — 이번엔 **(A) 일본어 출력만** (한국어는 보류):

**(A) 일본어 출력 [최우선] — 출력 언어 = 일본어, CosyVoice zero-shot과 동일조건 비교**
```
おはようございます、先生。今日もよろしくお願いします！
ねえ先生、ちょっと聞いてほしいことがあるんです。
今日も一日、本当にお疲れさまでした。もう遅いですから、そろそろ休みましょう？
さっきから先生のことばかり考えていました。
```

→ 생성 wav를 들으며 **음색 닮음 / 발음·억양 / 잡음 / 캐릭터성(활기)** 채점.
→ 결정 기준: **CosyVoice zero-shot 대비 fine-tune이 평탄화·노이즈·음질 천장을 개선하는가** (진행 문서 Stage 1 한계 항목과 직접 비교).

**(B) 한국어 출력 [보류]** — 한국어 레퍼런스 음원 확보 후 별도 진행. v2로 학습해 뒀으므로 재학습 없이 가능.

## 7. 체크포인트 다운로드 (로컬 추론용)

```python
from google.colab import files
# 경로는 학습 로그에서 확인된 최신 파일명으로
files.download('/content/GPT-SoVITS/SoVITS_weights_v2/arisu_e8_s....pth')
files.download('/content/GPT-SoVITS/GPT_weights_v2/arisu-e10.ckpt')
```

받은 `.pth`/`.ckpt`는 추후 로컬 WSL GPT-SoVITS 설치 후 `scripts/try/try_clone.py --model gptsovits`로 재생/RTF 측정에 사용.

---

## 주의

- **Colab 세션은 끊긴다** — 학습 중 브라우저·탭 유지. 끊기면 포맷팅부터 재실행. 체크포인트는 끝나는 즉시 다운로드.
- **무료 T4 한도** — 길어지면 끊길 수 있음. epoch 보수적으로.
- 결과(채점·결정)는 `docs/progress.md`에 D3 근거로 기록.
