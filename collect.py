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
    '자동차': ['현대차 기아 모비스 해외 생산', 'global automotive production Hyundai Kia'],
    'CKD·물류': ['CKD 자동차 부품 수출 물류', 'shipping freight port supply chain'],
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
                for e in feed.entries[:15]:
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
          "정책은 발언/제안/검토/확정 시행을 구분한다. "
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
    title = a.get('title', '').lower()
    desc = a.get('description', '').lower()
    body = title + ' ' + desc
    # A promotional headline is not an operational article merely because it names an automaker.
    if has_any(title, PROMOTION):
        return None
    if has_any(body, POLICY):
        return '산업·업무'
    if has_any(body, AUTO) and has_any(body, OPERATIONS + LOGISTICS):
        return '산업·업무'
    # Direct shipping/logistics developments are relevant without an automotive keyword.
    if has_any(title, LOGISTICS) and has_any(body, ('운임','선복','선적','지연','중단','혼잡','파업','비용','인상','급등','규제','차질','freight','shipping','delay','disruption','congestion','strike','rates')):
        return '산업·업무'
    if has_any(title, MAJOR):
        return '일반 뉴스'
    return None

def is_work(a):
    return a.get('section') == '산업·업무' if a.get('section') else classify(a) == '산업·업무'

def editorial_score(a):
    title = a.get('title','').lower()
    desc = a.get('description','').lower()
    body = title + ' ' + desc
    score = 0
    if has_any(title, AUTO): score += 12
    if has_any(title, OPERATIONS): score += 12
    if has_any(title, LOGISTICS): score += 15
    if has_any(body, POLICY): score += 18
    if has_any(desc, OPERATIONS): score += 4
    if has_any(title, MAJOR): score += 18
    if a.get('category') == '관심 키워드': score += 3
    return score

def choose_for_day(articles, existing, work_limit=12, general_limit=3):
    """Preserve previously published articles; select only relevant new headlines."""
    by_id = {a['id']: a for a in existing if a.get('id')}
    for article in articles:
        if article.get('id') and article['id'] not in by_id:
            a = dict(article)
            section = classify(a)
            if section is None:
                continue
            a['section'] = section
            by_id[a['id']] = a
    all_items = list(by_id.values())
    work = sorted((a for a in all_items if is_work(a)), key=lambda a:(editorial_score(a), a.get('published','')), reverse=True)
    general = sorted((a for a in all_items if not is_work(a)), key=lambda a:(editorial_score(a), a.get('published','')), reverse=True)
    # Never delete already-published articles or existing AI summaries.
    keep = {a['id']:a for a in existing if a.get('id')}
    new_work_slots = max(0, work_limit - sum(is_work(a) for a in existing))
    new_general_slots = max(0, general_limit - sum(not is_work(a) for a in existing))
    for group, limit in ((work, new_work_slots), (general, new_general_slots)):
        if limit <= 0:
            continue
        added = 0
        for a in group:
            if a['id'] not in keep:
                keep[a['id']] = a
                added += 1
                if added >= limit:
                    break
            if limit == 0:
                break
    return sorted(keep.values(), key=lambda a:a.get('published',''), reverse=True)

def main():
    articles = fetch()
    if not articles:
        print('No RSS entries found; existing archive unchanged.')
        return
    path = DATA / f'{TODAY}.json'
    try:
        old = json.loads(path.read_text(encoding='utf-8')).get('articles', []) if path.exists() else []
    except Exception:
        old = []
    # Rank before summarization; do not spend tokens on discarded headlines.
    chosen = choose_for_day(articles, old)
    old_map = {a['id']: a for a in old if a.get('id')}
    pending = [a for a in chosen if not a.get('ai_summary')]
    pending.sort(key=lambda a:(is_work(a), editorial_score(a), a.get('published','')), reverse=True)
    # Maximum 25 per run; once a day's selected articles are summarized,
    # subsequent runs do not re-summarize them.
    budget = max(0, int(os.environ.get('MAX_AI_ARTICLES_PER_RUN', '25')))
    candidates = pending[:budget]
    selected = summarize(candidates) if candidates else []
    updates = {a['id']:a for a in selected}
    merged = []
    for a in chosen:
        if a.get('ai_summary'):
            merged.append(a)
        elif updates.get(a['id'], {}).get('ai_summary'):
            merged.append(updates[a['id']])
        else:
            merged.append(old_map.get(a['id'], fallback([a])[0]))
    merged.sort(key=lambda a:a.get('published',''), reverse=True)
    new_items = [a for a in chosen if a['id'] not in old_map]
    result = {'date': TODAY, 'updated_at': NOW.isoformat(), 'article_count': len(merged),
              'ai_enabled': any(a.get('ai_summary') for a in merged), 'articles': merged}
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    index = sorted([p.stem for p in DATA.glob('????-??-??.json')], reverse=True)
    (DATA / 'index.json').write_text(json.dumps({'dates': index, 'latest': index[0] if index else None,
          'updated_at': NOW.isoformat()}, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Saved', TODAY, len(merged), 'articles; new candidates', len(new_items))
if __name__ == '__main__': main()
