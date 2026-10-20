"""Collect public RSS headlines and publish a daily CKD news digest."""
import os, json, re, time, hashlib, datetime, html
from pathlib import Path
from urllib.parse import quote
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo
import feedparser
import requests

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
DATA.mkdir(exist_ok=True)
KST = ZoneInfo('Asia/Seoul')
NOW = datetime.datetime.now(KST)
TODAY = NOW.strftime('%Y-%m-%d')
QUERIES = {
    '자동차': ['현대차 기아 모비스 해외 생산', 'global automotive production Hyundai Kia', '현대자동차 해외공장 생산능력 증설', '현대차 인도 공장 생산 차종', 'Hyundai India plant production capacity'],
    'CKD·물류': ['CKD 자동차 부품 수출 물류', 'shipping freight port supply chain', '현대차 기아 해외공장 부품 현지조달', '자동차 부품 선적 해상운임 선복'],
    '전동화': ['전기차 배터리 EREV HEV', 'electric vehicle battery industry'],
    '경제·금융': ['한국 경제 금리 환율 증시', 'world economy central bank inflation'],
    '정치·정책': ['한국 정치 정부 정책', 'Trump tariff White House executive order statement'],
    '국제·외교': ['세계 국제 외교 분쟁 주요 뉴스', 'world diplomacy geopolitics breaking news'],
    '국내 주요 사건': ['한국 주요 사건 사회 재난', '대한민국 주요 사건 사고'],
}
def shared_keywords():
    url=os.environ.get('SUPABASE_URL','').rstrip('/')
    key=os.environ.get('SUPABASE_ANON_KEY','')
    if not url or not key:
        print('Shared keywords not configured; using built-in queries only')
        return []
    try:
        r=requests.get(url+'/rest/v1/news_keywords',params={'select':'keyword'},headers={'apikey':key,'Authorization':'Bearer '+key},timeout=20)
        r.raise_for_status()
        return [x['keyword'] for x in r.json() if isinstance(x.get('keyword'),str)][:100]
    except Exception as ex:
        print('Shared keywords unavailable:',ex)
        return []

HEADERS = {'User-Agent': 'CKDNewsBriefing/1.0 (+RSS reader)'}

def norm(s):
    s = re.sub(r'<[^>]+>', ' ', html.unescape(s or ''))
    return re.sub(r'\s+', ' ', s).strip()

def fetch():
    articles = []
    for category, terms in {**QUERIES, '관심 키워드':shared_keywords()}.items():
        for term in terms:
            url = f'https://news.google.com/rss/search?q={quote(term + " when:2d")}&hl=ko&gl=KR&ceid=KR:ko'
            try:
                feed = feedparser.parse(url, request_headers=HEADERS)
                for e in feed.entries[:25]:
                    title = norm(e.get('title', ''))
                    link = e.get('link', '')
                    if not title or not link: continue
                    published = e.get('published', '')
                    try: dt = parsedate_to_datetime(published).astimezone(KST)
                    except Exception: dt = NOW
                    if (NOW-dt).total_seconds() > 60*60*48: continue
                    source = e.get('source', {})
                    publisher = source.get('title', '') if hasattr(source,'get') else ''
                    articles.append({'id': hashlib.sha1((title+link).encode()).hexdigest()[:14], 'title': title, 'url': link, 'source': publisher or 'Google News RSS', 'published': dt.isoformat(), 'category': category, 'description': norm(e.get('summary',''))[:450]})
            except Exception as ex:
                print('RSS error:', term, str(ex))
            time.sleep(.15)
    unique = {}
    for a in articles:
        key = re.sub(r'[^\w가-힣]', '', re.sub(r'\s*[-|]\s*[^-|]+$', '', a['title']).lower())[:65]
        if key and key not in unique: unique[key] = a
    return sorted(unique.values(), key=lambda x:x['published'], reverse=True)

def fallback(items):
    out=[]
    for a in items:
        a=dict(a)
        a['summary']='AI 요약 대기 중입니다. 원문 기사에서 내용을 확인할 수 있습니다.'
        a['impact']='AI 분석 대기 중' if a.get('section') == '산업·업무' else ''
        a['importance']='보통'
        a['section']=a.get('section') or classify(a) or '일반 뉴스'
        a['detail']={'background':'기사 전문을 확인하지 못해 상세 요약을 제공하지 않습니다.','key_points':[],'what_to_watch':'원문 및 공식 후속 발표 확인','fact_check':'제목과 RSS 발췌문만 수집됨'}
        out.append(a)
    return out

def summarize(items):
    """Summarize every supplied article, without selecting or discarding any.

    Batches are intentionally small to stay within model output limits.  When
    Gemini fails, keep RSS-only entries for a later retry rather than inventing
    a summary.  Already summarized entries never enter this function.
    """
    key = os.environ.get('GEMINI_API_KEY', '').strip()
    if not key:
        print('GEMINI_API_KEY missing; leaving articles for later AI summary')
        return fallback(items)
    model = os.environ.get('GEMINI_MODEL', 'gemini-3.1-flash-lite')
    endpoint = 'https://generativelanguage.googleapis.com/v1beta/models/' + model + ':generateContent'
    results = []
    for offset in range(0, len(items), 5):
        batch = items[offset:offset + 5]
        prompt = ("너는 CKD 자동차 수출포장 및 국내외 주요 뉴스 브리핑 편집자다. "
          "입력은 RSS 기사 제목·발췌문·날짜·링크일 뿐 기사 전문이 아니다. "
          "제공되지 않은 사실·수치·발언을 절대 지어내지 말고 불확실성은 명시한다. "
          "입력된 모든 기사 각각에 대해 반드시 하나의 결과를 반환하라. 기사를 선정하거나 생략하지 마라. "
          "정책은 발언/제안/검토/확정 시행을 구분한다. 정치인의 비판이나 주장은 사실로 단정하지 않고 발언 주체를 명시한다. 현재 정부를 과거 정부로 표현하지 말고, 시점이 확인되지 않은 과거 사례를 임의로 추가하지 마라. "
          "인명·정부명·한자 약칭을 추측해서 풀어 쓰지 마라. 李정부는 이명박 정부가 아니며, 불확실하면 원문 표기를 유지한다. "
          "각 기사마다 id, summary(한국어 2~3문장), impact(CKD 관련성이 없으면 "
          "'직접적인 업무 영향 확인되지 않음'), importance('높음' 또는 '보통'), "
          "detail 객체(background: 근거 기반 2~3문장, key_points: 주요 내용 문자열 배열 2~4개, "
          "what_to_watch: 확인할 후속 발표, fact_check: RSS 정보 한계)를 작성한다. "
          "JSON 객체 {\"articles\":[...]}만 출력하며 입력에 없는 id는 생성하지 않는다. "
          "입력 자료: " + json.dumps(batch, ensure_ascii=False))
        lookup = {a['id']: a for a in batch}
        completed = set()
        try:
            resp = requests.post(endpoint, headers={'x-goog-api-key': key, 'Content-Type': 'application/json'},
                json={'contents': [{'parts': [{'text': prompt}]}],
                      'generationConfig': {'responseMimeType': 'application/json', 'temperature': 0.2}},
                timeout=110)
            resp.raise_for_status()
            raw = resp.json()['candidates'][0]['content']['parts'][0]['text']
            parsed = json.loads(raw)
            for x in parsed.get('articles', []):
                if not isinstance(x, dict) or x.get('id') not in lookup or x['id'] in completed:
                    continue
                if not isinstance(x.get('summary'), str) or not x['summary'].strip():
                    continue
                article = dict(lookup[x['id']])
                for field in ('summary', 'impact', 'importance'):
                    article[field] = norm(str(x.get(field, '')))
                article['section'] = article.get('section') or classify(article) or '일반 뉴스'
                detail = x.get('detail', {})
                article['detail'] = detail if isinstance(detail, dict) else {}
                article['ai_summary'] = True
                results.append(article)
                completed.add(article['id'])
            print('Gemini summarized', len(completed), 'of', len(batch), 'articles in batch')
        except Exception as ex:
            print('Gemini batch unavailable; retry next run:', str(ex)[:400])
        results.extend(fallback([a for a in batch if a['id'] not in completed]))
    return results


# Article-level classification: RSS query category alone is not evidence of work relevance.
WORK_CATEGORIES = {'자동차', 'CKD·물류', '전동화', '관심 키워드'}
AUTO = ('현대차','현대자동차','기아','현대모비스','모비스','자동차','완성차','차량','전기차','배터리','전동화','erev','hev','ckd','hyundai','kia','mobis','automotive','automaker','vehicle','ev production')
OPERATIONS = ('생산','증산','감산','가동','공장','투자','신설','수출','수입','부품','공급','납품','발주','판매','실적','관세','무역','현지화','조달','파업','중단','리콜','생산량','물량','supply','production','factory','plant','export','tariff','investment','shipment','sales','sourcing')
LOGISTICS = ('해운','선복','선적','항만','컨테이너','운임','운송','물류','항공화물','화물','공급망','통관','수출규제','shipping','freight','container','port congestion','supply chain','logistics','customs')
POLICY = ('자동차 관세','부품 관세','전기차 보조금','자동차 규제','무역협정','수입 규제','수출 통제','현지 생산','전기차 정책','자동차 산업','ev tariff','auto tariff','vehicle tariff')
MAJOR = ('금리 결정','기준금리','환율 급등','환율 급락','증시 급락','증시 폭락','서킷브레이커','금융위기','국가비상사태','대규모 재난','지진','태풍','전쟁','휴전','정상회담','대통령 탄핵','총선 결과','대선 결과','중앙은행','무역 전쟁','긴급 제재','interest rate decision','central bank','ceasefire','earthquake','financial crisis','stock market crash','sanctions')
PROMOTION = ('축제','페스티벌','런페스타','마라톤','행사 참가','표창','수상','봉사활동','사회공헌','기부','캠페인','홍보대사','도서 출간','신간','책 소개','경제공부','맛집','운세','연예','아이돌','드라마','영화','festival','award ceremony','book review','celebrity')
GENERAL_NOISE = ('지지율 조사','여론조사','운세','맛집','연예','드라마','영화','아이돌','도서','책 소개','표창','수상','축제','페스티벌')

def has_any(text, terms):
    return any(term in text for term in terms)

def classify(a):
    """Classify by article substance, never by the search keyword/category."""
    title = a.get('title', '').lower()
    desc = a.get('description', '').lower()
    body = title + ' ' + desc
    if irrelevant_work_article(a):
        return None
    # Exclude clearly low-value coverage from BOTH sections.
    reject = PROMOTION + ('농구','프로농구','경기 프리뷰','연승','연패','카지노',
        '스포츠','주가,','주가 상승','주가 하락','장중','목표주가','목표가','부품주','관련주','매수 온기','매수세','주가 강세',
        '시사뒷담','지지율','여론조사','정치판','모빌리티 데이’ 개최')
    if has_any(title, reject):
        return None
    # Banking/financial expansion is not automotive supply-chain news merely
    # because a battery factory is mentioned as historical context.
    finance = ('은행', '금융', 'bank', 'banking')
    non_auto_business = ('방산', '원전', '부동산', '보험', '대출', '채권')
    if has_any(title, finance) and has_any(title, non_auto_business) and not has_any(title, ('자동차 부품', '자동차 생산', '완성차 공급', 'ckd 물류')):
        return None
    if ('모비스' in title and ('로봇' in title or '로보틱스' in title or '기술 동맹' in title or '오픈이노베이션' in title)):
        return '산업·업무'
    if ('battery materials production' in title or 'ev battery' in title) and ('delay' in title or 'production' in title):
        return '산업·업무'
    if has_any(body, POLICY):
        return '산업·업무'
    if has_any(title, AUTO) and has_any(body, OPERATIONS + LOGISTICS):
        return '산업·업무'
    if has_any(title, LOGISTICS) and has_any(body, ('운임','선복','선적','지연','중단','혼잡','파업','비용','인상','급등','규제','차질','freight','shipping','delay','disruption','congestion','strike','rates')):
        return '산업·업무'
    if has_any(title, ('코스피 폭락','증시 폭락','7%대 폭락','미국발 인플레이션','금리 결정','환율 급등','환율 급락','중앙은행','대규모 재난','전쟁','휴전','정상회담')):
        return '일반 뉴스'
    if has_any(title, MAJOR):
        return '일반 뉴스'
    return None

# Sports teams and celebrity promotions are never CKD/automotive work news.
SPORTS_PUBLICITY = ('농구','프로농구','농구단','시투','시구','홈경기','골프단','골프대회','골프 선수','배구단','축구단','야구단','스포츠단','치어리더','걸그룹','팬미팅','선수 영입','선수단','응원단','플레이오프','올스타전')
def irrelevant_work_article(a):
    return has_any((a.get('title') or '').lower(), SPORTS_PUBLICITY)

def is_work(a):
    return classify(a) == '산업·업무'

def editorial_score(a):
    title = a.get('title','').lower()
    body = title + ' ' + a.get('description','').lower()
    score = 0
    if has_any(title, AUTO): score += 12
    # Concrete overseas production plans take precedence over broad technology trends.
    if has_any(title, ('해외공장','신공장','생산능력','생산 체계','생산체계','증설','양산','푸네','pune','첸나이','chennai')) and has_any(body, AUTO + ('인도','india')): score += 24
    if has_any(title, ('로봇','로보틱스','아틀라스','robotics','atlas')) and not has_any(title, ('자동차 생산','부품 생산','공장 가동')): score -= 12
    if has_any(title, OPERATIONS): score += 12
    if has_any(title, LOGISTICS): score += 15
    if has_any(body, POLICY): score += 18
    if has_any(title, MAJOR): score += 18
    if a.get('category') == '관심 키워드': score += 2
    if a.get('importance') == '높음': score += 2
    return score

def topic_key(a):
    """Conservatively collapse repeated reports of the same clear event."""
    title = a.get('title','').lower()
    if (('현대차' in title or '현대자동차' in title or '기아' in title or 'hyundai' in title or 'kia' in title)
            and ('자율주행' in title or 'autonomous' in title)
            and ('트럭' in title or '대형트럭' in title or 'truck' in title)
            and any(w in title for w in ('실증', '시범', '물류 운송', '물류 노선', '본격 돌입', 'test', 'pilot'))):
        return 'hyundai-autonomous-truck-pilot'
    if ('모비스' in title or 'mobis' in title) and ('로봇' in title or '로보틱스' in title or '기술 동맹' in title or '오픈이노베이션' in title) and ('중국' in title or '상하이' in title or '中' in a.get('title','')):
        return 'mobis-china-robotics'
    if ('코스피' in title or '국내 증시' in title) and ('폭락' in title or '급락' in title):
        return 'korean-market-plunge'
    if ('현대차' in title or '현대자동차' in title or 'hyundai motor' in title or '현대차그룹' in title) and ('푸네' in title or 'pune' in title) and ('부품사' in title or '협력사' in title or '동반진출' in title or 'supply' in title):
        return 'hyundai-pune-suppliers'
    if ('현대차' in title or '현대자동차' in title or 'hyundai motor' in title or '현대차그룹' in title) and ('인도' in title or 'india' in title) and ('투자' in title or 'invest' in title) and not ('푸네' in title or 'pune' in title):
        return 'hyundai-india-investment'
    # Same headline distributed with different publisher suffixes.
    return re.sub(r'[^\w가-힣]','',re.sub(r'\s*[-|]\s*[^-|]+$','',title))[:75]

def same_day_issue(a, b):
    """Conservative same-event matching across differently worded headlines.

    Require a common concrete subject and action; never merge merely because
    the companies, industry, or broad topic are the same.
    """
    from difflib import SequenceMatcher
    ta = re.sub(r'\s*[-|]\s*[^-|]+$', '', a.get('title', '')).lower()
    tb = re.sub(r'\s*[-|]\s*[^-|]+$', '', b.get('title', '')).lower()
    if topic_key(a) == topic_key(b):
        return True
    def autonomous_truck(t):
        return (any(w in t for w in ('현대차', '현대자동차', '기아', 'hyundai', 'kia'))
                and '자율주행' in t and '트럭' in t
                and any(w in t for w in ('실증', '실제 물류', '물류 노선', '물류 운송', '시범 운행', '본격 돌입')))
    if autonomous_truck(ta) and autonomous_truck(tb):
        return True
    # A very high headline overlap can identify syndicated rewrites, but
    # only with shared meaningful words and no conflicting numerical details.
    tokens = lambda t: set(re.findall(r'[가-힣a-z0-9]{2,}', t)) - {
        '현대차', '기아', '현대자동차', '자동차', '관련', '사업', '시장', '산업', '올해', '내년'}
    aa, bb = tokens(ta), tokens(tb)
    if len(aa & bb) < 3 or min(len(aa), len(bb)) < 4:
        return False
    overlap = len(aa & bb) / max(1, min(len(aa), len(bb)))
    return overlap >= .85 and SequenceMatcher(None, ta, tb).ratio() >= .78


def merge_same_day_groups(groups):
    """Merge only confidently identical issues within the same section."""
    merged = []
    for (section, _), members in groups.items():
        matched = False
        for entry in merged:
            if entry['section'] != section:
                continue
            # Require every incoming article to match a representative, to
            # avoid chaining several different stories into one broad topic.
            if same_day_issue(members[0], entry['members'][0]):
                entry['members'].extend(members)
                matched = True
                break
        if not matched:
            merged.append({'section': section, 'members': list(members)})
    return [((entry['section'], str(i)), entry['members']) for i, entry in enumerate(merged)]


def representative_score(a):
    """Prefer a substantive, already summarized report over the first RSS hit."""
    title = a.get('title', '')
    description = a.get('description', '')
    detail = a.get('detail') or {}
    points = detail.get('key_points') or []
    if not isinstance(points, list):
        points = []
    score = editorial_score(a)
    score += min(len(description), 300) / 45
    score += min(len(a.get('summary', '')), 350) / 65
    score += min(len(points), 4) * 2
    score += 5 if a.get('ai_summary') else 0
    score += 3 if re.search(r'\d+(?:[.,]\d+)?\s*(?:억|조|만|대|곳|%|달러|원|billion|million)', title.lower()) else 0
    score -= 8 if a.get('source', '').lower() in ('naver blog', 'google news rss') else 0
    return score


def choose_for_day(articles, existing, work_limit=12, general_limit=3):
    """Select issue representatives; retain up to three source links per issue.

    Existing article summaries are never rewritten. Related items remain in
    the archive, and the public payload gets a small related_articles array.
    """
    by_id = {a['id']: dict(a) for a in existing if a.get('id')}
    for a in articles:
        if a.get('id'):
            by_id.setdefault(a['id'], dict(a))

    groups = {}
    # Filter before ranking so older archived items cannot consume daily slots.
    cutoff = NOW - datetime.timedelta(hours=24)
    for a in by_id.values():
        try:
            published_dt = datetime.datetime.fromisoformat(a['published'])
            if not (cutoff <= published_dt <= NOW):
                continue
        except (ValueError, KeyError, TypeError):
            continue
        section = classify(a)
        if section is None:
            continue
        groups.setdefault((section, topic_key(a)), []).append(a)

    ranked_groups = []
    for (section, _), members in merge_same_day_groups(groups):
        # Multiple Google News URLs can point to the exact same published
        # story. Keep one per normalized publisher+headline before grouping.
        unique_members = {}
        for member in members:
            headline = re.sub(r'[^\w가-힣]', '', re.sub(r'\s*[-|]\s*[^-|]+$', '', member.get('title', '').lower()))
            publisher = member.get('source', '').strip().lower()
            dedup_key = (publisher, headline)
            previous = unique_members.get(dedup_key)
            if previous is None or representative_score(member) > representative_score(previous):
                unique_members[dedup_key] = member
        members = list(unique_members.values())
        members.sort(key=lambda a: (representative_score(a), a.get('published', '')), reverse=True)
        representative = dict(members[0])
        representative['section'] = section
        representative['group_members'] = [
            {'id': a.get('id'), 'title': a.get('title', ''), 'description': a.get('description', ''),
             'source': a.get('source', ''), 'published': a.get('published', ''), 'url': a.get('url', '')}
            for a in members[:4]
        ]
        representative['related_articles'] = [
            {'title': a.get('title', ''), 'url': a.get('url', ''),
             'source': a.get('source', ''), 'published': a.get('published', '')}
            for a in members[1:4] if a.get('url')
        ]
        ranked_groups.append((editorial_score(representative), representative_score(representative), representative))

    ranked_groups.sort(key=lambda x: (x[0], x[1], x[2].get('published', '')), reverse=True)
    chosen, counts = [], {'산업·업무': 0, '일반 뉴스': 0}
    for _, _, a in ranked_groups:
        section = a['section']
        limit = work_limit if section == '산업·업무' else general_limit
        if counts[section] >= limit:
            continue
        chosen.append(a)
        counts[section] += 1
    return sorted(chosen, key=lambda a: a.get('published', ''), reverse=True)

def recent_history(days=7):
    """Read only earlier public digests; never alter historical JSON files."""
    out = []
    for offset in range(1, days + 1):
        day = (NOW - datetime.timedelta(days=offset)).strftime('%Y-%m-%d')
        path = DATA / (day + '.json')
        if not path.exists():
            continue
        try:
            for article in json.loads(path.read_text(encoding='utf-8')).get('articles', []):
                if isinstance(article, dict):
                    out.append({'date': day, 'article': article})
        except (ValueError, OSError) as ex:
            print('History unavailable:', day, ex)
    return out


def same_issue_prior(a, history):
    """Conservative match: only a recognized issue key or an identical headline."""
    key = topic_key(a)
    return [h for h in history if classify(h['article']) == a.get('section')
            and topic_key(h['article']) == key][:3]


def ai_editorial_review(items, history):
    """AI checks factual novelty against 7-day history and merges same-day RSS snippets.

    Failure defaults to retaining the story, never silently dropping it.
    """
    key = os.environ.get('GEMINI_API_KEY', '').strip()
    if not key or not items:
        return items
    model = os.environ.get('GEMINI_MODEL', 'gemini-3.1-flash-lite')
    endpoint = 'https://generativelanguage.googleapis.com/v1beta/models/' + model + ':generateContent'
    result = []
    for start in range(0, len(items), 4):
        batch = items[start:start + 4]
        payload = []
        for a in batch:
            related = a.get('group_members') or []
            previous = same_issue_prior(a, history)
            payload.append({
                'id': a['id'], 'current': [
                    {'title': m.get('title'), 'description': m.get('description'), 'source': m.get('source')}
                    for m in related[:4]
                ] or [{'title': a.get('title'), 'description': a.get('description')}],
                'previous': [
                    {'date': h['date'], 'title': h['article'].get('title'),
                     'summary': h['article'].get('summary'),
                     'key_points': (h['article'].get('detail') or {}).get('key_points', [])}
                    for h in previous
                ]
            })
        prompt = ("너는 뉴스 편집자다. 입력은 RSS 제목/발췌문과 최근 7일 브리핑 기록이며 기사 전문이 아니다. "
                  "동일한 사건에 대해 전일 보도 대비 확인 가능한 새로운 사실(수치/일정/결정/참여사/변경사항)이 "
                  "명확하게 없을 때만 status='repeat'로 분류하라. 이전 기사와의 동일성 또는 새 사실 유무가 "
                  "불명확하면 status='keep'으로 보수적으로 유지하라. 이전 기사가 없는 경우 무조건 keep. "
                  "같은 날 관련 기사들에서 확인되는 정보를 종합하되 RSS에 없는 사실을 만들지 마라. "
                  "기사 제목만 다르다는 이유로 새로운 사실이라고 판단하지 마라. 정치인의 비판·주장은 사실로 단정하지 말고 발언 주체를 명시하라. 현재 정부를 과거 정부로 서술하거나 근거 없는 과거 정책 사례를 만들지 마라. "
                  "정치인·정부명·한자 약칭을 추측해서 풀어 쓰지 마라. 특히 李정부를 이명박 정부로 해석하지 말고, 불확실하면 원문 표기를 유지하라. "
                  "각 id마다 status(keep/repeat), summary(한국어 2~3문장), "
                  "new_information(전일 대비 추가/변경 내용, 없으면 빈 문자열), "
                  "impact(발주·선적·포장·통관·해외생산에 관한 명시적 근거가 없는 경우 반드시 '직접적인 업무 영향 확인되지 않음'으로 작성. 기업명만으로 CKD 물량 변화나 직접 영향을 추정하지 마라), "
                  "importance(높음/보통), detail 객체(background, key_points 배열 2~4개, "
                  "what_to_watch, fact_check)를 포함하라. 전일 기록이 없는 경우 new_information은 빈 문자열. "
                  "JSON 객체 {\"articles\":[...]}만 출력. 입력: " + json.dumps(payload, ensure_ascii=False))
        try:
            resp = requests.post(endpoint, headers={'x-goog-api-key': key, 'Content-Type': 'application/json'},
                json={'contents': [{'parts': [{'text': prompt}]}],
                      'generationConfig': {'responseMimeType': 'application/json', 'temperature': 0.1}}, timeout=110)
            resp.raise_for_status()
            parsed = json.loads(resp.json()['candidates'][0]['content']['parts'][0]['text'])
            found = {x['id']: x for x in parsed.get('articles', []) if isinstance(x, dict) and x.get('id')}
        except Exception as ex:
            print('Editorial review unavailable, keeping stories:', str(ex)[:250])
            result.extend(batch)
            continue
        for a in batch:
            x = found.get(a['id'])
            if not x or not isinstance(x.get('summary'), str) or not x['summary'].strip():
                result.append(a)
                continue
            if x.get('status') == 'repeat' and same_issue_prior(a, history):
                print('Omitting repeated prior-day issue:', a['title'][:75])
                continue
            b = dict(a)
            # Keep original individual summary as an audit trail before adding a merged one.
            if b.get('ai_summary') and not b.get('original_summary'):
                b['original_summary'] = b.get('summary', '')
                b['original_detail'] = b.get('detail', {})
            for field in ('summary', 'impact', 'importance'):
                if isinstance(x.get(field), str) and x[field].strip():
                    b[field] = norm(x[field])
            if isinstance(x.get('detail'), dict):
                b['detail'] = x['detail']
            b['new_information'] = norm(x.get('new_information', ''))
            previous = same_issue_prior(a, history)
            if previous and b['new_information']:
                b['follow_up'] = {'date': previous[0]['date'],
                    'title': previous[0]['article'].get('title', ''),
                    'summary': previous[0]['article'].get('summary', ''),
                    'url': previous[0]['article'].get('url', '')}
            b['ai_summary'] = True
            b['integrated_summary'] = True
            b['integration_signature'] = b.get('integration_signature_candidate', '')
            result.append(b)
    return result


def integration_signature(a, history):
    members = a.get('group_members') or []
    previous = same_issue_prior(a, history)
    # Stable across repeated runs and changes only if input evidence changes.
    content = {'current': sorted([(m.get('id'), m.get('title'), m.get('description')) for m in members]),
               'previous': sorted([(h['date'], h['article'].get('id'), h['article'].get('summary')) for h in previous])}
    return hashlib.sha1(json.dumps(content, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def correct_known_name_error(a):
    """Repair a known cached political-summary error without rewriting other news."""
    if '李정부' not in a.get('title', '') or '정점식' not in a.get('title', ''):
        return a
    a = dict(a)
    a['summary'] = ("정점식 의원은 최근 증시 폭락과 관련해 이재명 정부가 국민연금을 "
                    "동원해 증시를 정치적으로 부양했다고 주장하며 비판했습니다. "
                    "이는 정 의원의 주장으로, 실제 국민연금 동원 여부나 증시 하락의 원인은 기사 제목만으로 확인할 수 없습니다.")
    a['original_summary'] = a['summary']
    a['detail'] = {
        'background': '최근 증시 하락을 둘러싼 정치권의 정부 정책 비판을 다룬 기사입니다.',
        'key_points': [
            '정점식 의원이 이재명 정부의 국민연금 동원 의혹을 제기',
            '증시 하락 원인에 관한 정치적 주장으로, 인과관계는 확인되지 않음'
        ],
        'what_to_watch': '정부·국민연금 측의 공식 설명과 관련 근거 공개 여부',
        'fact_check': 'RSS 제목에 인용된 의원 발언만 확인되며 실제 국민연금 운용 내역과 증시 영향은 검증되지 않음'
    }
    a['original_detail'] = dict(a['detail'])
    return a


def article_fingerprint(a):
    title = re.sub(r'\s*[-|]\s*[^-|]+$', '', a.get('title', '')).lower()
    return re.sub(r'[^\w가-힣]', '', title)


def previously_published(a, history):
    """Exact repeats are never republished; distinct follow-ups go to AI review."""
    fp = article_fingerprint(a)
    url = a.get('url', '').split('#')[0].rstrip('/')
    return any(article_fingerprint(h['article']) == fp or
               (url and h['article'].get('url', '').split('#')[0].rstrip('/') == url)
               for h in history)


def retry_older_pending(days=14, limit=12):
    """Complete historical public summaries without changing finished analyses."""
    remaining = limit
    for offset in range(1, days + 1):
        if remaining <= 0: break
        day = (NOW - datetime.timedelta(days=offset)).strftime('%Y-%m-%d')
        path = DATA / (day + '.json')
        if not path.exists(): continue
        try: payload = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError): continue
        articles = payload.get('articles', [])
        pending = [a for a in articles if not a.get('ai_summary') and not a.get('integrated_summary')]
        if not pending: continue
        replacements = {a['id']: a for a in summarize(pending[:remaining]) if a.get('ai_summary')}
        remaining -= min(len(pending), remaining)
        if not replacements: continue
        payload['articles'] = [replacements.get(a.get('id'), a) for a in articles]
        payload['ai_enabled'] = any(a.get('ai_summary') for a in payload['articles'])
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        archive_path = DATA / 'archive' / (day + '.json')
        if archive_path.exists():
            try:
                archive = json.loads(archive_path.read_text(encoding='utf-8'))
                archive['articles'] = [replacements.get(a.get('id'), a) for a in archive.get('articles', [])]
                archive_path.write_text(json.dumps(archive, ensure_ascii=False, indent=2), encoding='utf-8')
            except (OSError, ValueError): pass
        print('Completed previous pending summaries:', day, len(replacements))



def extract_article_image(url):
    """Best-effort OG/Twitter article image; never use a guessed stock photo."""
    try:
        from bs4 import BeautifulSoup
        from urllib.parse import urljoin, urlparse
        p=urlparse(url)
        if p.scheme not in ('http','https') or not p.hostname:return ''
        r=requests.get(url,headers={'User-Agent':'Mozilla/5.0'},timeout=5,stream=True)
        if r.status_code!=200 or 'html' not in r.headers.get('Content-Type','').lower():return ''
        raw=b''
        for chunk in r.iter_content(8192):
            raw+=chunk
            if len(raw)>140000:break
        r.close()
        soup=BeautifulSoup(raw,'html.parser')
        for selector in ('meta[property="og:image"]','meta[name="twitter:image"]','meta[property="og:image:url"]'):
            tag=soup.select_one(selector)
            if tag and tag.get('content'):
                image=urljoin(url,tag['content'].strip())
                if urlparse(image).scheme=='https':return image
    except Exception:pass
    return ''

def main():
    articles = fetch()
    if not articles:
        print('No new RSS entries; rechecking saved articles.')
    path = DATA / f'{TODAY}.json'
    try:
        old = json.loads(path.read_text(encoding='utf-8')).get('articles', []) if path.exists() else []
    except Exception:
        old = []
    # Preserve the full original collection and all completed AI summaries in
    # a separate archive, even when an item is no longer published.
    archive_dir = DATA / 'archive'
    archive_dir.mkdir(exist_ok=True)
    archive_path = archive_dir / f'{TODAY}.json'
    try:
        archived = json.loads(archive_path.read_text(encoding='utf-8')).get('articles', []) if archive_path.exists() else []
    except Exception:
        archived = []
    all_saved = {a['id']: correct_known_name_error(a) for a in archived if a.get('id')}
    for a in old:
        if a.get('id'):
            all_saved[a['id']] = correct_known_name_error(a)
    # Save new RSS candidates too, so grouped related links survive later runs.
    for a in articles:
        if a.get('id'):
            all_saved.setdefault(a['id'], a)
    # Archive first; re-running cannot erase a previously saved AI summary.
    archive_path.write_text(json.dumps({'date':TODAY, 'articles':list(all_saved.values())}, ensure_ascii=False, indent=2), encoding='utf-8')
    # Exclude exact prior publications before ranking, not after filling slots.
    history = recent_history(14)
    eligible = [a for a in all_saved.values() if not previously_published(a, history)]
    chosen = choose_for_day([], eligible)
    chosen = [a for a in chosen if not irrelevant_work_article(a)]
    # Require a genuinely recent publication, not merely a newly collected RSS hit.
    cutoff = NOW - datetime.timedelta(hours=24)
    def in_window(a):
        try: return cutoff <= datetime.datetime.fromisoformat(a['published']) <= NOW
        except (ValueError, KeyError, TypeError): return False
    # Strict publication window: never revive old stories just because today's
    # digest contained them. Same-issue follow-ups are reviewed separately.
    chosen = [a for a in chosen if in_window(a)
              and not previously_published(a, history)]
    old_map = all_saved
    # Reuse previous integrated summary unless today's sources or 7-day comparison changed.
    review_needed, merged = [], []
    for a in chosen:
        sig = integration_signature(a, history)
        a['integration_signature_candidate'] = sig
        cached = old_map.get(a['id'], {})
        if cached.get('integrated_summary') and cached.get('integration_signature') == sig:
            saved = dict(cached)
            saved['related_articles'] = a.get('related_articles', [])
            saved['section'] = a.get('section', saved.get('section'))
            merged.append(correct_known_name_error(saved))
        else:
            review_needed.append(a)
    budget = max(0, int(os.environ.get('MAX_AI_ARTICLES_PER_RUN', '25')))
    reviewed = ai_editorial_review(review_needed[:budget], history)
    reviewed_ids = {a['id'] for a in reviewed}
    for a in reviewed:
        if a.get('integrated_summary') or a.get('ai_summary'):
            merged.append(a)
        else:
            merged.extend(summarize([a]))
    # Preserve unreviewed entries and allow a future run to finish their AI analysis.
    for a in review_needed[budget:]:
        merged.append(a if a.get('ai_summary') else fallback([a])[0])
    # If AI review failed to produce a result for an article, it is retained by
    # ai_editorial_review; only explicit 'repeat' findings are omitted.
    for a in merged:
        a.pop('group_members', None)
        a.pop('integration_signature_candidate', None)
    merged = [correct_known_name_error(a) for a in merged if not irrelevant_work_article(a)]
    for a in merged:
        if not a.get('image_url') and a.get('url'):
            a['image_url']=extract_article_image(a['url'])
    merged.sort(key=lambda a:a.get('published',''), reverse=True)
    new_items = [a for a in chosen if a['id'] not in old_map]
    # Retain newly completed summaries even if the public selection changes later.
    for a in merged:
        all_saved[a['id']] = correct_known_name_error(a)
    archive_path.write_text(json.dumps({'date':TODAY, 'articles':list(all_saved.values())}, ensure_ascii=False, indent=2), encoding='utf-8')
    result = {'date': TODAY, 'updated_at': NOW.isoformat(), 'article_count': len(merged),
              'ai_enabled': any(a.get('ai_summary') for a in merged), 'articles': merged}
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    index = sorted([p.stem for p in DATA.glob('????-??-??.json')], reverse=True)
    (DATA / 'index.json').write_text(json.dumps({'dates': index, 'latest': index[0] if index else None,
          'updated_at': NOW.isoformat()}, ensure_ascii=False, indent=2), encoding='utf-8')
    retry_older_pending(limit=max(0, int(os.environ.get('MAX_BACKLOG_AI_ARTICLES', '12'))))
    print('Saved', TODAY, len(merged), 'articles; new candidates', len(new_items))
if __name__ == '__main__': main()
