"""Best-effort Naver most-read newsroom ranking collector.

Naver ranking is per publisher, NOT a site-wide popularity order.
Only publicly available titles/snippets are summarized; no fabricated text.
"""
import datetime, hashlib, json, os, re
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parent
KST=ZoneInfo('Asia/Seoul')
NOW=datetime.datetime.now(KST)
DATE=NOW.strftime('%Y-%m-%d')
URL='https://news.naver.com/main/ranking/popularDay.naver'
HEADERS={'User-Agent':'Mozilla/5.0 (compatible; NewsBriefing/1.0)'}

def clean(s): return re.sub(r'\s+',' ',s or '').strip()
def collect():
    r=requests.get(URL,headers=HEADERS,timeout=25);r.raise_for_status()
    soup=BeautifulSoup(r.text,'html.parser')
    found=[];seen=set()
    for block in soup.select('.rankingnews_box'):
        publisher=clean((block.select_one('.rankingnews_name') or block.select_one('strong')) .get_text(' ',strip=True)) if block.select_one('.rankingnews_name, strong') else '네이버 뉴스'
        for link in block.select('a[href]'):
            href=urljoin(URL,link.get('href',''))
            if not re.search(r'/(article|mnews/article)/\d+/\d+',href): continue
            title=clean(link.get_text(' ',strip=True))
            if len(title)<12:continue
            canonical=urlparse(href)._replace(query='',fragment='').geturl()
            if canonical in seen:continue
            seen.add(canonical)
            found.append({'id':hashlib.sha1(canonical.encode()).hexdigest()[:14], 'title':title,'url':canonical,'source':publisher,'published':DATE,'summary':'','category':'일반','ranking_source':'네이버 언론사별 많이 본 뉴스'})
    return found

def select_and_summarize(candidates):
    key=os.getenv('GEMINI_API_KEY','').strip()
    if not key:return []  # Do not publish unverified AI summaries
    endpoint='https://generativelanguage.googleapis.com/v1beta/models/'+os.getenv('GEMINI_MODEL','gemini-3.1-flash-lite')+':generateContent'
    prompt=('너는 한국 뉴스 편집자다. 제공한 네이버 언론사별 인기 기사 제목만을 근거로 주요 사회 이슈를 골라라. '
        '전체 통합 랭킹이 아님. 정치 경제 사회 국제 문화 생활의 다양성을 고려해 최대 15건 선정. '
        '동일 사건·재탕 보도 중복 제외. 기사 본문이 없으므로 제목에서 확인되는 사실만 한 문장으로 요약하고 '
        '제목만으로 판단할 수 없는 세부 내용은 지어내지 말 것. 선정된 기사 id, category(정치/경제/사회/국제/생활·문화/기타), '
        'summary를 배열 articles로 반환. JSON만 출력. 입력: '+json.dumps(candidates[:130],ensure_ascii=False))
    response=requests.post(endpoint,headers={'x-goog-api-key':key,'Content-Type':'application/json'},json={'contents':[{'parts':[{'text':prompt}]}], 'generationConfig':{'responseMimeType':'application/json','temperature':0.1}},timeout=110)
    response.raise_for_status()
    data=json.loads(response.json()['candidates'][0]['content']['parts'][0]['text'])
    lookup={a['id']:a for a in candidates}
    out=[];used=set()
    for item in data.get('articles',[]):
        a=lookup.get(item.get('id'))
        if not a or a['id'] in used or len(out)>=15:continue
        summary=clean(item.get('summary',''))
        if not summary:continue
        used.add(a['id']);out.append({**a,'category':item.get('category','기타'),'summary':summary,'summary_basis':'제목 기반 AI 요약 · 본문 미검증'})
    return out

def main():
    target=ROOT/'data'/'popular';target.mkdir(exist_ok=True)
    path=target/(DATE+'.json')
    try:
        candidates=collect()
        if not candidates:raise ValueError('Ranking article links not found; page layout or access may have changed')
        articles=select_and_summarize(candidates)
        if not articles:raise ValueError('AI summary unavailable; preserving prior saved data')
        path.write_text(json.dumps({'date':DATE,'updated_at':NOW.isoformat(),'source_url':URL,'articles':articles,'note':'네이버 언론사별 인기 기사 참고 후 AI 재선정. 전체 통합 인기 순위가 아님. 제목 기반 요약.'},ensure_ascii=False,indent=2),encoding='utf-8')
        print('Saved Naver popular:',len(articles))
    except Exception as exc:
        print('Naver popular collection skipped:',str(exc)[:300])
        # Existing data is never overwritten with an empty result.

if __name__=='__main__':main()
