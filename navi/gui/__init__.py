"""GUI 앱 — pywebview 창(별도 프로세스)이 데몬 컨트롤 플레인을 띄운다 (Stage 15 PR ③).

실행: python -m navi.gui   (데몬과 별개 프로세스 — GUI 죽어도 나비는 산다)
프런트는 static/index.html 단일 파일(바닐라 JS, 빌드 스텝 0). 서빙은 컨트롤 플레인
GET / (같은 오리진 — CORS 없음), 창은 그 URL을 여는 껍데기다(추후 Tauri 래핑 경로 보존).
"""
