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
                article['section'] = '산업·업무' if article['category'] in ('자동차', 'CKD·물류', '전동화', '관심 키워드') else '일반 뉴스'
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


# Editorial caps for new daily editions. Existing AI summaries are retained.
WORK_CATEGORIES = {'자동차', 'CKD·물류', '전동화', '관심 키워드'}
WORK_TERMS = ('ckd','현대차','기아','모비스','현대모비스','부품','생산','공장','수출','해운','선박','운임','항만','물류','관세','전기차','배터리','erev','hev','현지','공급망','파렛트','포장','hyundai','kia','mobis','shipping','freight','tariff','automotive','factory','export','supply chain')
MAJOR_TERMS = ('속보','긴급','발표','확정','시행','합의','인상','인하','제재','전쟁','휴전','정상회담','대통령','정부','금리','환율','재난','사망','대규모','선거','법안','관세','중앙은행','긴급회의','breaking','major','election','sanction','ceasefire','central bank')
MINOR_TERMS = ('연예','방송','드라마','영화','아이돌','패션','맛집','운세','스포츠','경기 결과','celebrity','entertainment')

def is_work(a):
    return a.get('category') in WORK_CATEGORIES

def editorial_score(a):
    title = a.get('title','').lower()
    desc = a.get('description','').lower()
    score = 20 if is_work(a) else 0
    score += min(24, sum(4 for term in WORK_TERMS if term in title))
    score += min(12, sum(2 for term in WORK_TERMS if term in desc))
    score += min(20, sum(5 for term in MAJOR_TERMS if term in title))
    score -= 30 if any(term in title for term in MINOR_TERMS) else 0
    if a.get('category') == '관심 키워드': score += 8
    if a.get('ai_summary'): score += 2
    return score

def choose_for_day(articles, existing, work_limit=15, general_limit=5):
    """Select important headlines, preserve existing AI-summarized entries.

    For an edition already populated under the old 60-article policy, do not
    silently delete the user's previously summarized entries. On a new day,
    the tighter caps apply from the start.
    """
    by_id = {a['id']: a for a in existing if a.get('id')}
    for a in articles:
        by_id.setdefault(a['id'], a)
    all_items = list(by_id.values())
    work = sorted((a for a in all_items if is_work(a)), key=lambda a:(editorial_score(a), a.get('published','')), reverse=True)
    general = sorted((a for a in all_items if not is_work(a) and editorial_score(a) >= 5), key=lambda a:(editorial_score(a), a.get('published','')), reverse=True)
    # Keep already AI-summarized entries even if this exceeds the cap for
    # editions originally produced with the older publication rules.
    keep = {a['id']:a for a in existing if a.get('ai_summary') and a.get('id')}
    for a in work[:work_limit] + general[:general_limit]:
        keep.setdefault(a['id'],a)
    return sorted(keep.values(),key=lambda a:a.get('published',''),reverse=True)

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
    # Maximum 25 per run; once a day's 20 selected articles are summarized,
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
