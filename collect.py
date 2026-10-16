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
    key=os.environ.get('OPENAI_API_KEY')
    if not key: return fallback(items)
    from openai import OpenAI
    client=OpenAI(api_key=key)
    pool=items[:100]
    prompt='''아래는 RSS로 수집한 뉴스의 제목, 짧은 발췌문, 링크와 날짜다. 원문 전문을 읽지 않았으므로 주어진 정보에 없는 사실을 만들지 마라. 같은 사건은 하나만 선택한다. 한국어로 일반 뉴스 5~10건과 산업·업무 뉴스 5~10건을 각각 고르고 국내외 주요 뉴스와 자동차/CKD/물류 뉴스를 균형 있게 포함한다. 각 기사별 id, summary(근거 있는 1~2문장), impact(자동차 CKD 수출포장, 생산물량, 선적, 통관 관련성이 확인되는 경우에만 구체적으로, 아니면 '직접적인 업무 영향 확인되지 않음'), importance('높음' 또는 '보통'), detail({background:기사 배경 2~3문장,key_points:확인 가능한 주요 사실 2~4개 배열,what_to_watch:향후 확인할 발표·변수,fact_check:발언/검토/확정 구분 및 정보 한계})를 JSON 객체 {"articles":[...]}로 출력하라. 입력에 없는 ID를 만들지 마라. 제목만으로 추정한 내용은 추정임을 명시하라.''' 
    try:
        response=client.chat.completions.create(model=os.environ.get('OPENAI_MODEL','gpt-4.1-mini'),response_format={'type':'json_object'},messages=[{'role':'system','content':prompt},{'role':'user','content':json.dumps(pool,ensure_ascii=False)}],temperature=0.2)
        parsed=json.loads(response.choices[0].message.content)
        lookup={a['id']:a for a in pool}
        out=[]
        for x in parsed.get('articles',[]):
            if x.get('id') not in lookup: continue
            a=dict(lookup[x['id']]); a.update({k:norm(str(x.get(k,''))) for k in ('summary','impact','importance')});a['section']='산업·업무' if a['category'] in ('자동차','CKD·물류','전동화','관심 키워드') else '일반 뉴스';a['detail']=x.get('detail',{}) if isinstance(x.get('detail'),dict) else {};out.append(a)
            if len(out)>=20:break
        return out if len(out)>=5 else fallback(items)
    except Exception as ex:
        print('AI error:',str(ex));return fallback(items)

def main():
    articles=fetch()
    if not articles:
        print('No RSS entries found; existing archive unchanged.')
        return
    selected=summarize(articles)
    result={'date':TODAY,'updated_at':NOW.isoformat(),'article_count':len(selected),'ai_enabled':bool(os.environ.get('OPENAI_API_KEY')),'articles':selected}
    (DATA/f'{TODAY}.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    index=sorted([p.stem for p in DATA.glob('????-??-??.json')],reverse=True)
    (DATA/'index.json').write_text(json.dumps({'dates':index,'latest':index[0] if index else None,'updated_at':NOW.isoformat()},ensure_ascii=False,indent=2),encoding='utf-8')
    print('Saved',TODAY,len(selected),'articles')
if __name__=='__main__': main()
