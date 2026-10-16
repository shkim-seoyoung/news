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
    for a in items[:10]:
        a=dict(a)
        a['summary']='원문 제목을 확인하세요. AI 요약이 설정되지 않아 기사 본문을 요약하지 않았습니다.'
        a['impact']='원문 확인 후 업무 영향 판단 필요'
        a['importance']='보통'
        a['section']='산업·업무' if a['category'] in ('자동차','CKD·물류','전동화','관심 키워드') else '일반 뉴스'
        a['detail']={'background':'기사 전문을 확인하지 못해 상세 요약을 제공하지 않습니다.','key_points':[],'what_to_watch':'원문 및 공식 후속 발표 확인','fact_check':'제목과 RSS 발췌문만 수집됨'}
        out.append(a)
    return out

def summarize(items):
    """Use Gemini free-tier when available; never claim to have read full articles."""
    key = os.environ.get('GEMINI_API_KEY', '').strip()
    if not key:
        print('GEMINI_API_KEY missing; using RSS-only fallback')
        return fallback(items)
    pool = items[:30]
    prompt = ("너는 CKD 자동차 수출포장 및 국내외 주요 뉴스 브리핑 편집자다. "
      "입력은 RSS 기사 제목·발췌문·날짜·링크일 뿐 기사 전문이 아니다. "
      "제공되지 않은 사실·수치·발언을 절대 지어내지 말고 불확실성은 명시한다. "
      "산업·업무 기사와 일반 주요 뉴스를 골고루 선택하되, 중요도와 중복을 고려하여 최대 15건 선정한다. "
      "정책은 발언/제안/검토/확정 시행을 반드시 구분한다. "
      "각 기사마다 id, summary(한국어 2~3문장), impact(CKD 포장·물류·선적·생산 관련성이 "
      "확인되지 않으면 '직접적인 업무 영향 확인되지 않음'), importance('높음' 또는 '보통'), "
      "detail 객체(background: 근거 기반 2~3문장, key_points: 주요 내용 문자열 배열 2~4개, "
      "what_to_watch: 확인할 후속 발표, fact_check: RSS 정보 한계와 정책 확정 여부)를 작성한다. "
      "JSON 객체 {\"articles\":[...]}만 출력하며 입력에 없는 id는 생성하지 않는다. "
      "입력 자료: " + json.dumps(pool, ensure_ascii=False))
    model = os.environ.get('GEMINI_MODEL', 'gemini-3.1-flash-lite')
    endpoint = 'https://generativelanguage.googleapis.com/v1beta/models/' + model + ':generateContent'
    try:
        resp = requests.post(endpoint, headers={'x-goog-api-key': key, 'Content-Type': 'application/json'},
            json={'contents': [{'parts': [{'text': prompt}]}],
                  'generationConfig': {'responseMimeType': 'application/json', 'temperature': 0.2}},
            timeout=110)
        resp.raise_for_status()
        raw = resp.json()['candidates'][0]['content']['parts'][0]['text']
        parsed = json.loads(raw)
        lookup = {a['id']: a for a in pool}
        out, seen = [], set()
        for x in parsed.get('articles', []):
            if not isinstance(x, dict) or x.get('id') not in lookup or x['id'] in seen:
                continue
            a = dict(lookup[x['id']]); seen.add(a['id'])
            for field in ('summary', 'impact', 'importance'):
                a[field] = norm(str(x.get(field, '')))
            a['section'] = '산업·업무' if a['category'] in ('자동차', 'CKD·물류', '전동화', '관심 키워드') else '일반 뉴스'
            d = x.get('detail', {})
            a['detail'] = d if isinstance(d, dict) else {}
            a['ai_summary'] = True
            out.append(a)
            if len(out) >= 15: break
        if out:
            print('Gemini summarized', len(out), 'articles')
            return out
        print('Gemini returned no valid articles; using fallback')
    except Exception as ex:
        print('Gemini unavailable; RSS fallback:', str(ex)[:500])
    return fallback(items)

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
    # Preserve articles from earlier runs and avoid paying to summarize the same story again.
    old_ids = {a.get('id') for a in old}
    new_items = [a for a in articles if a['id'] not in old_ids]
    # Upgrade a few old RSS-only items if Gemini was configured later.
    retry = [a for a in old if not a.get('ai_summary')][:10] if os.environ.get('GEMINI_API_KEY') else []
    candidates = (retry + new_items)[:30]
    selected = summarize(candidates) if candidates else []
    by_id = {a['id']: a for a in old if a.get('id')}
    for a in selected:
        by_id[a['id']] = a
    merged = sorted(by_id.values(), key=lambda a: a.get('published', ''), reverse=True)[:60]
    result = {'date': TODAY, 'updated_at': NOW.isoformat(), 'article_count': len(merged),
              'ai_enabled': any(a.get('ai_summary') for a in merged), 'articles': merged}
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    index = sorted([p.stem for p in DATA.glob('????-??-??.json')], reverse=True)
    (DATA / 'index.json').write_text(json.dumps({'dates': index, 'latest': index[0] if index else None,
          'updated_at': NOW.isoformat()}, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Saved', TODAY, len(merged), 'articles; new candidates', len(new_items))
if __name__ == '__main__': main()
