# CKD NEWS 브리핑 사이트

반응형 정적 사이트 + GitHub Actions 일간 RSS 수집 + 선택적 OpenAI 요약.

## 배포 (GitHub Pages)
1. GitHub에서 **공개(Public) 저장소**를 만들고 이 폴더의 모든 파일(숨김 `.github` 포함)을 저장소 최상위에 업로드합니다.
2. 저장소 **Settings → Pages → Build and deployment → Deploy from a branch → main / (root) → Save**를 선택합니다.
3. **Settings → Secrets and variables → Actions → New repository secret**에 `OPENAI_API_KEY`를 입력하면 AI 요약/업무 영향 분석이 활성화됩니다. API 사용료가 별도로 발생할 수 있습니다. 키가 없으면 기사 제목을 수집하지만 본문 요약은 하지 않습니다.
4. **Actions → Daily CKD News → Run workflow**를 한 번 실행하면 최초 데이터가 생성됩니다. 이후 한국시간 매일 07:00에 실행을 요청합니다. GitHub Actions 예약 작업은 부하에 따라 늦게 시작하거나 건너뛸 수 있어 정시 갱신을 보장하지 않습니다.
5. 사이트 주소: `https://<github-사용자명>.github.io/<저장소명>/`.

## 주의사항
- RSS 제목과 짧은 설명만 사용합니다. 기사 전문을 무단 복제하지 않습니다.
- Google News RSS 링크는 뉴스 중계 링크일 수 있습니다. 클릭하면 해당 기사로 연결됩니다.
- 제목만으로 알 수 없는 내용은 추정하지 않도록 AI에 지시하지만, AI 결과는 반드시 원문과 대조하세요.
- 날짜별 JSON은 Git 저장소에 누적됩니다. 공개 저장소의 뉴스/분석 데이터는 누구나 열람할 수 있습니다.
- 뉴스 공급자의 RSS 형식·접근 정책 변경 시 수집 코드 수정이 필요합니다.
- 무료 정적 호스팅 자체는 서버 측 AI 실행을 제공하지 않습니다. GitHub Actions가 별도로 수집·저장합니다.
- 이미 설정한 ChatGPT 개인 자동 브리핑은 이 사이트와 별개입니다.

## 첫 시범 데이터
2026-10-16 브리핑 7건은 공개 기사 검색 결과를 바탕으로 수동 선정·요약한 샘플입니다. 자동 수집 실행 결과가 아닙니다. `index.html`을 직접 열어도 이 샘플을 볼 수 있습니다.


## v3: 로그인 없는 공동 키워드 관리
- 뉴스는 GitHub Pages, 키워드는 Supabase 무료 프로젝트의 `news_keywords` 테이블에 공유 저장합니다. GitHub Pages만으로는 브라우저에서 공통 데이터를 안전하게 쓰고 읽을 수 없습니다.
- Supabase 프로젝트 생성 → SQL Editor에서 `supabase-setup.sql` 실행 → `config.js`에 Project URL과 공개 `anon`/publishable 키 입력. **service_role/secret 키를 넣지 마세요.**
- GitHub 저장소 Settings → Secrets and variables → Actions에 `SUPABASE_URL`은 Variable, `SUPABASE_ANON_KEY`는 Secret으로 설정합니다.
- 팀원이 추가·삭제하면 즉시 Supabase에 반영되며, 다음 예약 수집 때 키워드를 읽습니다. 기본 수집 키워드는 계속 유지됩니다.
- 키워드 변경은 확인창을 거치지만, 로그인 없이 누구나 쓰기/삭제가 가능해 외부인도 변경할 수 있습니다. 데이터 오염·스팸·요청 남용 위험이 있으며 공개 운영 전 CAPTCHA, 속도 제한, 복구 대책이 필요합니다.
- Supabase 설정 전에는 키워드 관리가 비활성화되고, 기존 뉴스 조회 기능만 작동합니다.
- GitHub Actions의 예약 실행은 정시를 보장하지 않습니다. AI API 비용 및 Supabase 무료 플랜 정책은 별도 확인해야 합니다.
