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
    '자동차': ['global automakers production supply chain Toyota GM Ford Volkswagen', '글로벌 자동차 업계 생산 판매 동향', '현대차 기아 모비스 해외 생산', 'global automotive production Hyundai Kia', '현대자동차 해외공장 생산능력 증설', '현대차 인도 공장 생산 차종', 'Hyundai India plant production capacity'],
    'CKD·물류': ['Los Angeles Long Beach port terminal strike disruption', 'Savannah Mobile port congestion shipping delay', 'global port strike terminal disruption freight shipping', 'ocean carrier blank sailing port congestion automotive parts', '미국 항만 파업 선적 지연 운송 차질', 'CKD 자동차 부품 수출 물류', 'shipping freight port supply chain', '현대차 기아 해외공장 부품 현지조달', '자동차 부품 선적 해상운임 선복'],
    '전동화': ['전기차 배터리 EREV HEV', 'electric vehicle battery industry'],
    '경제·금융': ['한국 경제 금리 환율 증시', 'world economy central bank inflation'],
    '정치·정책': ['한국 정치 정부 정책', 'Trump tariff White House executive order statement'],
    '국제·외교': ['세계 국제 외교 분쟁 주요 뉴스', 'world diplomacy geopolitics breaking news'],
    '국내 주요 사건': ['한국 주요 사건 사회 재난', '대한민국 주요 사건 사고'],
}
# Priority markets served by the packing team (Beijing/Jiangsu separately).
MARKET_SEARCHES = [
 ('India', 'Hyundai Kia Mobis India Chennai Pune production supplier', 'en', 'IN'),
 ('USA', 'Hyundai Kia Mobis US Georgia Alabama production parts tariff', 'en', 'US'),
 ('Mexico', 'Hyundai Kia Mobis Mexico plant parts supply production', 'es', 'MX'),
 ('Brazil', 'Hyundai Kia Mobis Brasil fábrica produção fornecedores', 'pt-BR', 'BR'),
 ('China Beijing', '北京现代 现代摩比斯 工厂 生产 零部件', 'zh-CN', 'CN'),
 ('China Jiangsu', '江苏 现代摩比斯 工厂 生产 零部件', 'zh-CN', 'CN'),
]
MARKET_TERMS = {
 '인도': ('인도','india','chennai','첸나이','pune','푸네'),
 '미국': ('미국','usa','u.s.','georgia','조지아','alabama','앨라배마','savannah','사바나'),
 '멕시코': ('멕시코','mexico','méxico','몬테레이','monterrey'),
 '브라질': ('브라질','brazil','brasil','piracicaba','피라시카바'),
 '중국·북경': ('북경','베이징','beijing','北京'),
 '중국·강소': ('강소','장쑤','jiangsu','江苏'),
}
FOLLOWUP_WORDS = ('발표','확정','착공','가동','양산','증설','감산','중단','변경','확대','축소','투자','관세','조달','생산','계약','launch','start','production','capacity','investment','tariff','supply','plan','update')
STOP_TERMS = {'현대차','현대자동차','기아','모비스','현대모비스','자동차','부품','관련','산업','사업','시장','뉴스','올해','내년','전망','the','and','for','with','hyundai','kia','mobis','motor','auto','news','india','미국','인도','중국','멕시코','브라질'}

def shared_scraps(limit=80):
    """Read shared scrap records; failure never stops regular news collection."""
    url=os.environ.get('SUPABASE_URL','').rstrip('/')
    key=os.environ.get('SUPABASE_ANON_KEY','')
    if not url or not key: return []
    try:
        r=requests.get(url+'/rest/v1/news_scraps', params={
            'select':'article_key,article_data,created_at', 'order':'created_at.desc', 'limit':str(limit)},
            headers={'apikey':key,'Authorization':'Bearer '+key}, timeout=20)
        r.raise_for_status()
        return [x for x in r.json() if isinstance(x.get('article_data'),dict)]
    except Exception as ex:
        print('Shared scraps unavailable:',str(ex)[:180]);return []

def title_terms(title):
    title=re.sub(r'\s+[-|]\s+[^-|]+$', '', (title or '').lower())
    return [w for w in re.findall(r'[가-힣a-zA-Z]{2,}|[a-zA-Z]+[0-9]+|[0-9]+[a-zA-Z]+',title)
            if w not in STOP_TERMS and len(w)>=2][:12]

def scrap_queries(scraps, max_queries=12):
    """Use specific title entities; never search generic 'automotive news'."""
    queries=[];seen=set()
    for x in scraps:
        a=x['article_data'];words=title_terms(a.get('title',''))
        if len(words)<2:continue
        query=' '.join(words[:5]);key=query.lower()
        if key in seen:continue
        seen.add(key);queries.append(query)
        if len(queries)>=max_queries:break
    return queries

def scrap_relationship(a,scraps):
    """Conservative, explainable matching based on event anchors or title entities."""
    at=set(title_terms(a.get('title','')))
    if len(at)<2:return None
    best=None;best_score=0
    for x in scraps:
        old=x['article_data']
        if a.get('url') and a['url']==old.get('url'):continue
        if a.get('id') and a['id']==old.get('id'):continue
        bt=set(title_terms(old.get('title','')))
        shared=at & bt
        if len(shared)<2:continue
        overlap=len(shared)/max(1,min(len(at),len(bt)))
        if overlap<0.5:continue
        same_event=bool(event_anchor(a) and event_anchor(a)==event_anchor(old))
        # A near-identical reprint is related coverage, not a verified update.
        old_pub=old.get('published','')
        new_pub=a.get('published','')
        newer=bool(old_pub and new_pub and new_pub[:10]>old_pub[:10])
        if same_event and newer: relation='후속보도'
        elif newer and overlap>=0.78 and len(shared)>=3 and any(w in (a.get('title','').lower()) for w in FOLLOWUP_WORDS):relation='후속보도'
        else:relation='관련'
        score=overlap+(0.5 if same_event else 0)
        if score>best_score:
            best_score=score
            best={'type':relation,'title':old.get('title',''),'url':old.get('url',''),
                  'scrapped_at':x.get('created_at','')}
    return best

def market_tags(a):
    t=(a.get('title','')+' '+a.get('description','')).lower()
    return [name for name,terms in MARKET_TERMS.items() if any(w in t for w in terms)]

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
    scraps=shared_scraps()
    sources=[(cat,term,'ko','KR') for cat,terms in {**QUERIES,'관심 키워드':shared_keywords()}.items() for term in terms]
    sources += [('해외·'+region,query,lang,country) for region,query,lang,country in MARKET_SEARCHES]
    sources += [('스크랩 추적',query,'ko','KR') for query in scrap_queries(scraps)]
    for category, term, lang, country in sources:
            ceid=country+':'+lang
            url = f'https://news.google.com/rss/search?q={quote(term + " when:2d")}&hl={lang}&gl={country}&ceid={ceid}'
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
          "입력된 모든 기사 각각에 대해 반드시 하나의 결과를 반환하라. 기사를 선정하거나 생략하지 마라. " + WORK_PERSPECTIVE_RULES + GENERAL_NEWS_RULES + WORK_NEWS_RULES + 
          "정책은 발언/제안/검토/확정 시행을 구분한다. 정치인의 비판이나 주장은 사실로 단정하지 않고 발언 주체를 명시한다. 현재 정부를 과거 정부로 표현하지 말고, 시점이 확인되지 않은 과거 사례를 임의로 추가하지 마라. "
          "인명·정부명·한자 약칭을 추측해서 풀어 쓰지 마라. 李정부는 이명박 정부가 아니며, 불확실하면 원문 표기를 유지한다. "
          "기사 제목의 연도·시점과 모순되는 연도는 작성하지 마라. " 
          "각 기사마다 id, summary(한국어 2~3문장), impact(업무 관점, 일반 뉴스는 빈 문자열), importance('높음' 또는 '보통'), "
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
                results.append(sanitize_editorial(article))
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
    if event_anchor(a) == 'mobis-semiconductor-ecosystem':
        return '산업·업무'
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
    if a.get('category','').startswith('해외·') and (has_any(body, AUTO) and has_any(body, OPERATIONS + LOGISTICS) or (has_any(body, ('现代摩比斯','现代汽车','北京现代','mobis','hyundai','kia')) and has_any(body, ('生产','工厂','零部件','供应','产能','生产计划','produção','fábrica','peças','fornecedores','producción','planta','proveedores')))):
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
    if has_any(title, AUTO): score += 7
    if has_any(title, ('파업','항만 폐쇄','터미널 운영 중단','선적 제한','결품','port strike','terminal strike','port shutdown','port disruption','shipping disruption')): score += 42
    # Concrete overseas production plans take precedence over broad technology trends.
    if has_any(title, ('해외공장','신공장','생산능력','생산 체계','생산체계','증설','양산','푸네','pune','첸나이','chennai')) and has_any(body, AUTO + ('인도','india')): score += 24
    if has_any(title, ('로봇','로보틱스','아틀라스','robotics','atlas')) and not has_any(title, ('자동차 생산','부품 생산','공장 가동')): score -= 12
    if has_any(title, OPERATIONS): score += 12
    if has_any(title, LOGISTICS): score += 15
    if has_any(body, POLICY): score += 18
    if has_any(title, MAJOR): score += 18
    if a.get('category') == '관심 키워드': score += 0
    if a.get('importance') == '높음': score += 2
    if a.get('priority_markets'): score += min(15,8+3*len(a['priority_markets']))
    if a.get('scrap_relation'): score += 22 if a['scrap_relation']['type']=='후속보도' else 12
    return score

ANALYSIS_VERSION = 'event-diversity-risk-v6-20261029'

WORK_PERSPECTIVE_RULES = (
    "업무 뉴스는 자동차 산업·CKD·물류 동향을 파악하는 브리핑이다. 모든 기사를 CKD와 연결할 필요가 없다. "
    "직접적인 업무 관련 사실이 있으면 해당 영향과 확인할 사항을 설명한다. "
    "직접 관련이 없는 국내 신차, 경쟁사 투자, 전동화 기술 등도 중요하면 선정할 수 있으며 "
    "이 경우 impact에는 해당 산업 동향의 의미만 설명한다. "
    "예를 들어 국내 생산 기아 신차는 기아의 제품·기술 전략 관점에서 설명한다. "
    "테슬라·아우디 등 경쟁사 생산 계획으로 현대모비스 CKD 발주·포장 물량 변화를 추론하지 않는다. "
    "현대모비스 AS 수익이나 R&D 투자만으로 CKD 포장 인프라 투자 가능성을 주장하지 않는다. "
    "실제 근거 없는 숫자·항로·현지화율·공급 품목을 만들지 않는다. "
    "impact 필드는 사이트 호환을 위해 유지하며, 직접 업무 영향 또는 산업 동향 관점으로 작성한다. "
)

GENERAL_NEWS_RULES = (
    "일반 뉴스(section='일반 뉴스')는 주요 사실만 간결하게 요약한다. "
    "impact는 반드시 빈 문자열로 작성하고 CKD·부품·포장·선적과 억지로 연결하지 않는다. "
    "기사 제목과 RSS 발췌문에 없는 연도·수치·정책 시행 여부를 만들어내지 않는다. "
    "과거 기사나 시점이 불명확한 제목은 현재 진행 중인 사건이라고 단정하지 않는다. "
)
WORK_NEWS_RULES = (
    "자동차·CKD·물류 분야의 주요 산업 동향을 선정한다. "
    "담당 해외 법인 관련 생산·부품·조달·물류 변화는 우선한다. "
    "국내 신차와 경쟁사 투자도 산업적 의미가 있으면 선정할 수 있으나 CKD와 억지로 연결하지 않는다. "
    "직접 연관성이 없으면 impact는 산업 동향의 의미로만 작성한다. "
)
def sanitize_editorial(article):
    """Apply deterministic safeguards even when AI ignores prompt constraints."""
    a = dict(article)
    if a.get('section') == '일반 뉴스':
        a['impact'] = ''
    for field in ('impact', 'summary', 'source_comparison'):
        value = a.get(field, '')
        if isinstance(value, str):
            value = re.sub(r'(?i)\b[0-9a-f]{14,16}\b(?:와|과)?\s*(?:동일한 사건|동일 사건|관련 사건)', '기존 보도와 동일한 사건', value)
            a[field] = value
    title = a.get('title', '').lower()
    weak = (
        ('테슬라' in title or 'tesla' in title)
        or (('모비스' in title or 'mobis' in title)
            and any(x in title for x in ('as 이익', 'as 부품', 'r&d 투자', '연구개발 투자')))
    )
    if weak:
        impact = a.get('impact', '')
        if any(x in impact.lower() for x in ('ckd', '포장', '선적', '발주 물량', '인프라 투자')):
            a['impact'] = '해당 기업의 산업 동향이며 당사 CKD 발주·포장·선적 물량과의 직접적인 연관성은 확인되지 않았습니다.'
    # Never keep an explicitly mismatched year in AI-generated analysis.
    years = re.findall(r'(?<!\\d)20\\d{2}(?!\\d)', a.get('summary', ''))
    evidence = (a.get('title', '') + ' ' + a.get('description', ''))
    if any(y not in evidence for y in years):
        a['summary'] = '기사 제목과 RSS 발췌문만으로는 구체적인 연도 및 경과를 검증할 수 없습니다. 원문 확인이 필요합니다.'
        a['ai_summary'] = False
    return a

def event_anchor(a):
    """Identify a concrete announcement, not just a shared industry keyword."""
    title = a.get('title', '').lower()
    compact = re.sub(r'\s+', '', title)
    mobis = any(w in compact for w in ('현대모비스', '모비스', 'hyundaimobis', 'mobis'))
    semiconductor = any(w in compact for w in ('반도체', '車반도체', '차반도체', 'semiconductor'))
    event_words = ('국산', '개발', '협력', '동맹', '원팀', '생태계', '전주기', '53개', '13종', '2029', '29년', 'ask', '상용화', '팹리스', '전환', '사업화', '생산우선', '국내생산')
    if mobis and semiconductor and any(w in compact for w in event_words):
        # A specific product/vehicle rollout is a different event.
        if any(w in compact for w in ('ev3', '부분변경', 'led칩', 'led반도체')):
            return None
        return 'mobis-semiconductor-ecosystem'
    # General news: rate decision is one event, even across different wording.
    if any(w in compact for w in ('한국은행', '한은', 'bankofkorea')) and '기준금리' in compact and any(w in compact for w in ('동결', '유지', '결정')):
        return 'bok-base-rate-decision'
    return None


def concrete_event_key(a):
    """Match a specific reported event, not every story about one company."""
    t=(a.get('title') or '').lower().replace(' ', '')
    hy=any(k in t for k in ('현대차','현대자동차','hyundai'))
    if hy and any(k in t for k in ('앨라배마','alabama')) and any(k in t for k in ('투자','투입','invest','증설','쏜다')):
        return 'hyundai-alabama-plant-investment'
    if hy and any(k in t for k in ('3분기','3q','thirdquarter')) and any(k in t for k in ('실적','영업익','영업이익','수익성','판매부진')):
        return 'hyundai-q3-earnings-' + str(NOW.year)
    if any(k in t for k in ('la·롱비치','la롱비치','losangeles','longbeach','롱비치')) and any(k in t for k in ('파업','strike','터미널','terminal')):
        return 'la-long-beach-terminal-strike-' + str(NOW.year)
    return None

def event_facts(a):
    """Generalized event fingerprint based on actors, locations and actions.

    A common press-release story is not a new event just because a publisher
    changes the headline. Avoid merging generic stories about the same brand.
    """
    t = (a.get('title', '') + ' ' + a.get('description', '')).lower()
    t = re.sub(r'\s*[-|]\s*[^-|]{2,35}$', '', t)
    actors = {
        'hyundai': ('현대차', '현대자동차', 'hyundai motor'),
        'mobis': ('현대모비스', 'mobis'), 'kia': ('기아', 'kia '),
        'toyota': ('도요타', '토요타', 'toyota'), 'gm': ('제너럴모터스', 'general motors', 'gm '),
        'ford': ('포드', 'ford '), 'vw': ('폭스바겐', 'volkswagen'),
    }
    places = {
        'alabama': ('앨라배마', 'alabama'), 'pune': ('푸네', 'pune'),
        'chennai': ('첸나이', 'chennai'), 'georgia': ('조지아', 'georgia'),
        'la_lb_port': ('롱비치', 'long beach', 'la항', 'los angeles port', 'la·롱비치'),
        'savannah': ('서배너', '사바나', 'savannah'), 'mobile': ('모빌항', 'port of mobile'),
        'mexico': ('멕시코', 'mexico'), 'brazil': ('브라질', 'brazil'),
    }
    actions = {
        'investment': ('투자', '투입', '쏜다', 'investment', 'invests'),
        'earnings': ('영업익', '영업이익', '실적', '매출', 'earnings', 'profit'),
        'strike': ('파업', 'strike', 'walkout', '노조 협상'),
        'production': ('생산', '양산', '가동', 'production', 'manufacturing'),
        'shutdown': ('중단', '폐쇄', 'shutdown', 'closure'),
        'tariff': ('관세', 'tariff'), 'recall': ('리콜', 'recall'),
    }
    def hits(d): return [k for k, words in d.items() if any(w in t for w in words)]
    actor, place, action = hits(actors), hits(places), hits(actions)
    quarter = re.search(r'([1-4])\s*분기', t) or re.search(r'\bq([1-4])\b', t)
    # Specific event identity requires both an actor and place, or a named port
    # with a disruption. A single common keyword is never enough.
    if 'la_lb_port' in place and ('strike' in action or 'shutdown' in action):
        return 'event:port:la_lb:' + ('strike' if 'strike' in action else 'shutdown')
    if len(actor) == 1 and len(place) == 1 and 'investment' in action:
        return 'event:' + actor[0] + ':' + place[0] + ':investment'
    if len(actor) == 1 and 'earnings' in action and quarter:
        return 'event:' + actor[0] + ':earnings:q' + quarter.group(1) + ':' + str(NOW.year)
    return None


def canonical_event_key(a):
    """Stable, cross-day event ID for press-release rewrites and headline variations."""
    t = (a.get('title', '') + ' ' + a.get('description', '')).lower()
    hy = any(w in t for w in ('현대차', '현대자동차', 'hyundai motor'))
    year = re.search(r'20\d{2}', a.get('published', ''))
    yr = year.group(0) if year else str(NOW.year)
    if hy and any(w in t for w in ('3분기', '3q', 'third quarter')) and any(w in t for w in ('영업익','영업이익','매출','실적','수익성','판매 목표','판매목표','목표 하향','목표하향','판매 부진','판매 감소')):
        return 'event:hyundai:quarterly-results:q3:' + yr
    if hy and any(w in t for w in ('앨라배마', 'alabama')) and any(w in t for w in ('투자','투입','증설','확충','쏜다','invest')):
        return 'event:hyundai:alabama:investment:' + yr
    return None

def topic_key(a):
    """Conservatively collapse repeated reports of the same clear event."""
    anchor = canonical_event_key(a) or event_facts(a) or concrete_event_key(a) or event_anchor(a)
    if anchor:
        return anchor if anchor.startswith('event:') else 'event:' + anchor
    title = a.get('title','').lower()
    if (('현대차' in title or '현대자동차' in title or '기아' in title or 'hyundai' in title or 'kia' in title)
            and ('자율주행' in title or 'autonomous' in title)
            and ('트럭' in title or '대형트럭' in title or 'truck' in title)
            and any(w in title for w in ('실증', '시범', '물류 운송', '물류 노선', '본격 돌입', 'test', 'pilot'))):
        return 'hyundai-autonomous-truck-pilot'
    if ('모비스' in title or 'mobis' in title) and ('반도체' in title or 'semiconductor' in title or '차량용 칩' in title) and any(w in title for w in ('국산화', '국내', '개발', '양산', '공급망', '투자', 'localization', 'chip')):
        return 'mobis-vehicle-semiconductor-localization'
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


def choose_for_day(articles, existing, work_limit=None, general_limit=None):
    """Select issue representatives; retain up to three source links per issue.

    Group source articles before selecting a representative; retain links to
    the underlying reporting in the published JSON.
    """
    by_id = {a['id']: dict(a) for a in existing if a.get('id')}
    for a in articles:
        if a.get('id'):
            by_id.setdefault(a['id'], dict(a))

    groups = {}
    source_pool = list(by_id.values())
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
        # A single event may arrive through different RSS categories.
        # Anchor-based grouping preserves the work-news classification.
        anchor = event_anchor(a)
        group_section = '산업·업무' if anchor and anchor.startswith('mobis-') else section
        groups.setdefault((group_section, topic_key(a)), []).append(a)

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
        # Supplement the issue with older related reporting, without using
        # older articles as the representative or reviving an expired story.
        for candidate in source_pool:
            if candidate.get('id') in {m.get('id') for m in members}:
                continue
            try:
                dt = datetime.datetime.fromisoformat(candidate['published'])
                if not (NOW - datetime.timedelta(hours=48) <= dt <= NOW):
                    continue
            except (KeyError, ValueError, TypeError):
                continue
            if classify(candidate) != section or not same_day_issue(candidate, members[0]):
                continue
            members.append(candidate)
        members.sort(key=lambda a: (representative_score(a), a.get('published', '')), reverse=True)
        recent_members = []
        for m in members:
            try:
                if cutoff <= datetime.datetime.fromisoformat(m['published']) <= NOW:
                    recent_members.append(m)
            except (KeyError, ValueError, TypeError):
                pass
        if not recent_members:
            continue
        representative = dict(recent_members[0])
        representative['section'] = section
        relation_member=next((m for m in members if m.get('scrap_relation')),None)
        if relation_member:representative['scrap_relation']=relation_member['scrap_relation']
        representative['priority_markets']=sorted(set(t for m in members for t in m.get('priority_markets',[])))
        representative['group_members'] = [
            {'id': a.get('id'), 'title': a.get('title', ''), 'description': a.get('description', ''),
             'source': a.get('source', ''), 'published': a.get('published', ''), 'url': a.get('url', '')}
            for a in members[:30]
        ]
        representative['related_articles'] = [
            {'title': a.get('title', ''), 'url': a.get('url', ''),
             'source': a.get('source', ''), 'published': a.get('published', '')}
            for a in members[:30] if a.get('url') and a.get('id') != representative.get('id')
        ]
        ranked_groups.append((editorial_score(representative), representative_score(representative), representative))

    ranked_groups.sort(key=lambda x: (x[0], x[1], x[2].get('published', '')), reverse=True)
    chosen, counts = [], {'산업·업무': 0, '일반 뉴스': 0}
    for _, _, a in ranked_groups:
        section = a['section']
        limit = work_limit if section == '산업·업무' else general_limit
        if limit is not None and counts[section] >= limit:
            continue
        chosen.append(a)
        counts[section] += 1
    return sorted(chosen, key=lambda a: a.get('published', ''), reverse=True)

def recent_history(days=14):
    """Read previous published digests for actual cross-day event comparison."""
    out=[]
    for offset in range(1, days+1):
        day=(NOW-datetime.timedelta(days=offset)).strftime('%Y-%m-%d')
        path=DATA / (day+'.json')
        if not path.exists():
            continue
        try:
            records=json.loads(path.read_text(encoding='utf-8')).get('articles',[])
            out.extend({'date':day,'article':a} for a in records if isinstance(a,dict) and a.get('title'))
            archive = DATA / 'archive' / (day+'.json')
            if archive.exists():
                archived=json.loads(archive.read_text(encoding='utf-8')).get('articles',[])
                out.extend({'date':day,'article':a} for a in archived if isinstance(a,dict) and a.get('title') and a.get('ai_summary'))
        except (OSError,ValueError) as ex:
            print('History read error:',day,str(ex)[:120])
    return out


def same_issue_prior(a, history):
    """Conservative match: only a recognized issue key or an identical headline."""
    key = topic_key(a)
    return [h for h in history if (h['article'].get('section') or classify(h['article'])) == a.get('section')
            and (topic_key(h['article']) == key or same_day_issue(a, h['article']))][:5]


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
                'id': a['id'], 'event_key':topic_key(a), 'current': [
                    {'title': m.get('title'), 'description': m.get('description'), 'source': m.get('source')}
                    for m in related[:10]
                ] or [{'title': a.get('title'), 'description': a.get('description')}],
                'previous': [
                    {'date': h['date'], 'title': h['article'].get('title'),
                     'summary': h['article'].get('summary'),
                     'key_points': (h['article'].get('detail') or {}).get('key_points', []), 'event_key': topic_key(h['article'])}
                    for h in previous
                ]
            })
        prompt = ("너는 뉴스 편집자다. 입력은 RSS 제목/발췌문과 최근 7일 브리핑 기록이며 기사 전문이 아니다. "
                  "과거 기사와 같은 사건을 반복 설명하고 새 사실(투자 확정, 수치, 가동 일정, 생산능력, 참여사, 결정, 실제 진척)이 없으면 status='repeat'. "
                  "새 사실이 확인되면 status='keep' 및 new_information에 구체적 변경사항을 적어 후속보도로 분류한다. "
                  "같은 산업 주제지만 별개의 사건이면 신규 뉴스다. 인도 투자 계획과 인도 분기 실적은 서로 다른 사건이므로 후속보도가 아니다. 불명확하면 keep하되 신규 사실을 지어내지 않는다. "
                  "이전 기사가 없는 경우 keep. "
                  "같은 날 관련 기사들에서 확인되는 정보를 종합하되 RSS에 없는 사실을 만들지 마라. "
                  + WORK_PERSPECTIVE_RULES +
                  "기사 제목만 다르다는 이유로 새로운 사실이라고 판단하지 마라. 정치인의 비판·주장은 사실로 단정하지 말고 발언 주체를 명시하라. 현재 정부를 과거 정부로 서술하거나 근거 없는 과거 정책 사례를 만들지 마라. "
                  "정치인·정부명·한자 약칭을 추측해서 풀어 쓰지 마라. 특히 李정부를 이명박 정부로 해석하지 말고, 불확실하면 원문 표기를 유지하라. "
                  "각 id마다 status(keep/repeat), summary(한국어 2~3문장), "
                  "new_information(전일 대비 추가/변경 내용, 없으면 빈 문자열), "
                  "impact(직접 업무 관련 뉴스는 확인된 업무 관점, 그 외 자동차 뉴스는 산업 동향의 의미 1~3문장), "
                  "importance(높음/보통), source_comparison(관련 보도 간 확인 가능한 추가 정보·관점 차이. 차이가 없으면 빈 문자열), detail 객체(background, key_points 배열 2~4개, "
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
            # A missing AI response must not turn a known old event into 'new'.
            result.extend(a for a in batch if not same_issue_prior(a, history))
            continue
        for a in batch:
            x = found.get(a['id'])
            if not x or not isinstance(x.get('summary'), str) or not x['summary'].strip():
                if not same_issue_prior(a, history): result.append(a)
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
            b['source_comparison'] = norm(x.get('source_comparison', ''))
            previous = same_issue_prior(a, history)
            if previous and b['new_information']:
                b['news_relation'] = '후속보도'
                b['follow_up'] = {'date': previous[0]['date'],
                    'title': previous[0]['article'].get('title', ''),
                    'summary': previous[0]['article'].get('summary', ''),
                    'url': previous[0]['article'].get('url', '')}
            if previous and not b['new_information']:
                print('Omitting same-event story without new facts:', a['title'][:75])
                continue
            elif not previous:
                b['news_relation'] = '신규 뉴스'
            b['ai_summary'] = True
            b['integrated_summary'] = True
            b['integration_signature'] = b.get('integration_signature_candidate', '')
            result.append(b)
    return result


def integration_signature(a, history):
    members = a.get('group_members') or []
    previous = same_issue_prior(a, history)
    # Stable across repeated runs and changes only if input evidence changes.
    content = {'analysis_version': ANALYSIS_VERSION, 'current': sorted([(m.get('id'), m.get('title'), m.get('description')) for m in members]),
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
    scraps=shared_scraps()
    for a in articles:
        a['priority_markets']=market_tags(a)
        relation=scrap_relationship(a,scraps)
        if relation: a['scrap_relation']=relation
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
    for a in all_saved.values():
        a['priority_markets']=market_tags(a)
        relation=scrap_relationship(a,scraps)
        if relation:a['scrap_relation']=relation
        else:a.pop('scrap_relation',None)
    # Archive first; re-running cannot erase a previously saved AI summary.
    archive_path.write_text(json.dumps({'date':TODAY, 'articles':list(all_saved.values())}, ensure_ascii=False, indent=2), encoding='utf-8')
    # Group ALL current sources first: excluding previously published URLs here
    # would silently remove valuable related reporting from each event.
    history = recent_history(14)
    chosen = choose_for_day([], list(all_saved.values()))
    chosen = [a for a in chosen if not irrelevant_work_article(a)]
    # Require a genuinely recent publication, not merely a newly collected RSS hit.
    cutoff = NOW - datetime.timedelta(hours=24)
    def in_window(a):
        try: return cutoff <= datetime.datetime.fromisoformat(a['published']) <= NOW
        except (ValueError, KeyError, TypeError): return False
    # Strict publication window: never revive old stories just because today's
    # digest contained them. Same-issue follow-ups are reviewed separately.
    chosen = [a for a in chosen if in_window(a)]
    # Skip an event only if every source in the group was already published
    # and there is no new reporting. A repeated representative may still
    # carry newly collected related links and must not be discarded.
    chosen = [a for a in chosen if any(
        not previously_published(m, history) for m in a.get('group_members', [a]))]
    old_map = all_saved
    # Reuse integrated analysis only when the event's evidence signature matches.
    review_needed, merged = [], []
    for a in chosen:
        sig = integration_signature(a, history)
        a['integration_signature_candidate'] = sig
        cached = old_map.get(a['id'], {})
        if cached.get('integrated_summary') and cached.get('integration_signature') == sig:
            saved = dict(cached)
            saved['related_articles'] = a.get('related_articles', [])
            saved['priority_markets'] = a.get('priority_markets', [])
            if a.get('scrap_relation'):saved['scrap_relation']=a['scrap_relation']
            else:saved.pop('scrap_relation',None)
            saved['source_comparison'] = cached.get('source_comparison', '')
            saved['section'] = a.get('section', saved.get('section'))
            if same_issue_prior(saved,history) and not saved.get('new_information'):
                continue
            merged.append(sanitize_editorial(correct_known_name_error(saved)))
        else:
            review_needed.append(a)
    budget = max(0, int(os.environ.get('MAX_AI_ARTICLES_PER_RUN', '25')))
    reviewed = ai_editorial_review(review_needed[:budget], history)
    reviewed_ids = {a['id'] for a in reviewed}
    for a in reviewed:
        if same_issue_prior(a, history) and not a.get('new_information'):
            continue
        if a.get('integrated_summary') or a.get('ai_summary'):
            merged.append(a)
        else:
            merged.extend(summarize([a]))
    # Preserve unreviewed entries and allow a future run to finish their AI analysis.
    for a in review_needed[budget:]:
        if same_issue_prior(a, history):
            print('Deferring historical event until novelty can be checked:', a['title'][:75])
            continue
        merged.append(a if a.get('ai_summary') else fallback([a])[0])
    # If AI review failed to produce a result for an article, it is retained by
    # ai_editorial_review; only explicit 'repeat' findings are omitted.
    for a in merged:
        a.pop('group_members', None)
        a.pop('integration_signature_candidate', None)
    merged = [sanitize_editorial(correct_known_name_error(a)) for a in merged if not irrelevant_work_article(a)]
    for a in merged:
        if not a.get('image_url') and a.get('url'):
            a['image_url']=extract_article_image(a['url'])
    # Final defense: do not publish multiple headlines about one concrete event.
    # This also collapses legacy cached summaries from earlier buggy runs.
    grouped={}
    for a in merged:
        k=(a.get('section'),topic_key(a))
        prev=grouped.get(k)
        if prev is None:
            grouped[k]=a
        else:
            winner=max((prev,a),key=representative_score)
            loser=a if winner is prev else prev
            links=winner.setdefault('related_articles',[])
            if loser.get('url') and loser.get('url') != winner.get('url') and not any(x.get('url')==loser['url'] for x in links):
                links.append({k:loser.get(k,'') for k in ('title','url','source','published')})
            for item in loser.get('related_articles',[]):
                if item.get('url') and item['url']!=winner.get('url') and not any(x.get('url')==item['url'] for x in links):links.append(item)
            grouped[k]=winner
    merged=list(grouped.values())
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
