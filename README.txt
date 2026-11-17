CKD NEWS v13 — 전체 교체 패키지 (2026-11-17)

1. 저장소 최상위: collect.py, event_store.py, index.html 교체
2. .github/workflows/news.yml 교체 (예약 시간 변경 없음: 한국시간 07:00 / 12:00)
3. 기존 requirements.txt, naver_popular.py, data 폴더, GitHub Secrets 유지. 절대 삭제하지 마세요.
4. Actions > CKD News Auto Collect > Run workflow 수동 실행.
5. 실행 로그에서 Collection audit, AUDIT ZERO, Publisher URLs 확인.

수정: 네이버 검색 우회 제거; Google News 원문 URL 디코딩 시도; 디코딩 불가 시 잘못된 링크 대신 비활성 표시; 뉴스 선택 창 24h→48h; 후보 선택이 비었을 때 최근 후보 재검토; 0건 원인 진단 로그; 원래 예약 시간 유지.

주의: 원문 주소 디코딩은 외부 Google News 구조/네트워크에 따라 실패할 수 있습니다. 이 경우 검색 우회나 허위 원문 링크를 제공하지 않습니다. 0건을 무조건 기사로 채우지 않으며, 중복·품질 검사 결과에 따라 0건일 수 있습니다. 실제 GitHub Actions 및 Gemini 호출은 이 환경에서 검증하지 못했습니다.
