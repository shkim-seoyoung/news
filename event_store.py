"""Persistent event ledger; daily JSON files are views of events."""
import json,re,hashlib
from pathlib import Path
ACTORS={'kia':r'기아|\bkia\b','hyundai':r'현대차|현대자동차|hyundai motor','mobis':r'현대모비스|mobis','toyota':r'도요타|토요타|toyota','suzuki':r'스즈키|suzuki','benz':r'벤츠|mercedes'}
PLACES={'india':r'인도|india|印','mexico':r'멕시코|mexico','alabama':r'앨라배마|alabama','georgia':r'조지아|georgia','chennai':r'첸나이|chennai','pune':r'푸네|pune','savannah':r'사바나|서배너|savannah','longbeach':r'롱비치|long beach','brazil':r'브라질|brazil','china':r'중국|china'}
TYPES={'plant_investment':r'제\s*2\s*공장|두\s*번째\s*공장|공장.*(?:투자|증설|신설)|(?:투자|증설).*공장|plant.*(?:invest|expand|second)|(?:invest|second).*plant|생산능력.*(?:확대|프로젝트)|생산기지.*(?:확대|증설)|생산\s*63만|생산\s*60만', 'earnings':r'영업이익|영업익|실적\s*발표|분기\s*실적|earnings','strike':r'파업|strike|walkout','recall':r'리콜|recall','shutdown':r'공장.*(?:폐쇄|가동중단)|(?:폐쇄|가동중단).*공장|plant shutdown'}
def event_identity(a):
 t=str(a.get('title') or '').lower()
 actors=[k for k,v in ACTORS.items() if re.search(v,t,re.I)]
 places=[k for k,v in PLACES.items() if re.search(v,t,re.I)]
 if len(actors)>1:
  # A parent-group mention after the lead does not make this a multi-event roundup.
  lead=t[:12]
  lead_actors=[k for k,v in ACTORS.items() if re.search(v,lead,re.I)]
  if len(lead_actors)==1:actors=lead_actors
 if len(actors)!=1 or not places:return None
 kind=next((k for k,v in TYPES.items() if re.search(v,t,re.I)),None)
 if not kind:return None
 place=next((p for p in ('chennai','pune','alabama','georgia') if p in places),places[0])
 facility='second_plant' if re.search(r'제\s*2\s*공장|두\s*번째\s*공장|second\s+(?:india\s+)?plant',t) else 'plant'
 # The same specific second-plant announcement is also described as a capacity project.
 if kind=='plant_investment' and place=='india' and actors[0]=='kia' and re.search(r'60만|63만|20억|2[.]6조|2조\s*6|제\s*2\s*공장|두\s*번째\s*공장',t):facility='india_capacity_expansion'
 if kind!='plant_investment':facility='general'
 period=(a.get('published') or '')[:4] if kind=='plant_investment' else ''
 if kind=='earnings':
  m=re.search(r'([1-4])\s*분기|\bq([1-4])\b',t)
  if not m:return None
  period=(a.get('published') or '')[:4]+':q'+next(x for x in m.groups() if x)
 if kind in ('strike','shutdown'):period=(a.get('published') or '')[:7]
 return ':'.join(('event-v12',actors[0],place,kind,facility,period))
def same_event(a,b):
 x,y=event_identity(a),event_identity(b)
 if x and y:return x==y
 def norm(t):return re.sub(r'[^a-z0-9가-힣]','',re.sub(r'\s+[-|]\s+[^-|]+$','',str(t).lower()))
 x,y=norm(a.get('title','')),norm(b.get('title',''))
 return len(x)>15 and x==y
def _facts(a):
 t=str(a.get('title') or '').lower()
 return set(re.findall(r'착공\s*(?:확정|개시)|준공\s*(?:확정|완료)|가동\s*(?:시작|개시)|투자\s*철회|계획\s*취소|(?:착공|준공|가동)\s*일정\s*변경|construction\s+started|production\s+began',t))
class EventStore:
 def __init__(self,data,before_day=None):
  self.data=Path(data);self.path=self.data/'events.json';self.events={}
  if self.path.exists() and before_day is None:
   try:self.events=json.loads(self.path.read_text(encoding='utf-8')).get('events',{})
   except (OSError,ValueError,TypeError):pass
  for p in sorted(self.data.glob('????-??-??.json')):
   if before_day and p.stem >= before_day:continue
   try:articles=json.loads(p.read_text(encoding='utf-8')).get('articles',[])
   except (OSError,ValueError,TypeError):continue
   for a in articles:
    if isinstance(a,dict) and a.get('title'):self._insert(a,p.stem)
 def _key(self,a):
  key=event_identity(a)
  if key:return key
  t=re.sub(r'[^a-z0-9가-힣]','',str(a.get('title') or '').lower())[:180]
  return 'headline:'+hashlib.sha256(t.encode()).hexdigest()[:20] if len(t)>15 else None
 def _insert(self,a,day):
  key=self._key(a)
  if not key:return None
  if key not in self.events:
   self.events[key]={'id':hashlib.sha256(key.encode()).hexdigest()[:16],'title':a.get('title',''),'summary':a.get('summary',''),'url':a.get('url',''),'first_seen':day,'last_seen':day,'sources':[],'facts':[]}
  e=self.events[key];e['last_seen']=max(e['last_seen'],day)
  url=a.get('url','')
  if url and url not in e['sources']:e['sources'].append(url)
  e['facts']=sorted(set(e.get('facts',[]))|_facts(a))
  return e['id']
 def assess(self,a):
  e=self.events.get(self._key(a))
  if not e:return 'new',None
  if _facts(a)-set(e.get('facts',[])):return 'followup',e
  return 'repeat',e
 def record(self,a,day):return self._insert(a,day)
 def save(self):self.path.write_text(json.dumps({'version':12,'events':self.events},ensure_ascii=False,indent=2),encoding='utf-8')

def reconcile_public_history(data):
 """Move exact structured-event repeats into the first story's related links.
    Back up each modified day; never touch archives or ambiguous event IDs.
 """
 data=Path(data);first={};modified={};removed=[]
 for path in sorted(data.glob('????-??-??.json')):
  try:doc=json.loads(path.read_text(encoding='utf-8'))
  except (OSError,ValueError,TypeError):continue
  keep=[]
  for a in doc.get('articles',[]):
   key=event_identity(a)
   if key and key in first:
    original_path, original=first[key]
    links=original.setdefault('related_articles',[])
    seen={x.get('url') for x in links}|{original.get('url')}
    for item in [a]+a.get('related_articles',[]):
     if item.get('url') and item['url'] not in seen:
      links.append({k:item.get(k,'') for k in ('title','url','source','published')});seen.add(item['url'])
    modified[original_path]=True
    removed.append((path.name,a.get('title','')))
   else:
    keep.append(a)
    if key:first[key]=(path,a)
  if len(keep)!=len(doc.get('articles',[])):
   doc['articles']=keep;doc['article_count']=len(keep)
   modified[path]=doc
  elif path in modified and modified[path] is True:
   modified[path]=doc
 # Re-read documents for first stories that were updated after their file was visited.
 for path in list(modified):
  if modified[path] is True:
   doc=json.loads(path.read_text(encoding='utf-8'))
   for a in doc.get('articles',[]):
    key=event_identity(a)
    if key in first and first[key][0]==path:
     a['related_articles']=first[key][1].get('related_articles',[])
   modified[path]=doc
 for path,doc in modified.items():
  backup=path.with_suffix('.json.v12.bak')
  if not backup.exists():backup.write_bytes(path.read_bytes())
  path.write_text(json.dumps(doc,ensure_ascii=False,indent=2),encoding='utf-8')
 return removed
