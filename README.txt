CKD NEWS v14 — 원문 링크/기사 근거 개선

교체 파일: collect.py, event_store.py, index.html, .github/workflows/news.yml
기존 data/ 및 requirements.txt, naver_popular.py 유지.
news.yml의 예약 시간은 한국시간 오전 7시, 낮 12시로 변경 없음.

- Google News RSS 링크에서 언론사 직접 URL 추출: 리디렉션/HTML 메타데이터/Google News 디코더 순서로 시도
- 원문 URL 확인 시 original_url로 저장, 관련 기사도 처리
- 기사 본문 접근 가능 시 source_excerpt 및 content_status=publisher_excerpt 저장
- 본문 접근 불가 시 content_status=rss_only; 본문을 읽은 것처럼 AI에 전달하지 않음
- 원문 URL 확보 실패 시 Google News RSS 링크는 클릭 가능하게 유지(구글 차단 가능성은 남음)
- 수집/사건관리/스케줄은 기존 v13을 유지

주의: 실제 외부 네트워크에서의 Google News 디코딩 성공률은 환경별로 다름.
Google News 링크를 직접 원문 URL로 100% 변환할 수 있다고 보장하지 않음.
기존 날짜별 JSON의 원문 주소는 별도 재처리 필요.
