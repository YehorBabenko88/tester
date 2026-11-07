"""Official archive discovery and validated atomic downloads."""
import csv
import gzip
import hashlib
import json
import re
import uuid
from datetime import date,timedelta,datetime,timezone
from decimal import Decimal
from itertools import chain
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote,urljoin,urlsplit,unquote
import requests
from .journal import Journal,utcnow
from .transport import Transport

BASE='https://public.bybit.com'
ROOTS={'trading','spot','premium_index','spot_index','kline_for_metatrader4'}

def timestamp_fields(raw):
    value=Decimal(raw)
    if not value.is_finite() or value<0: raise ValueError('Invalid archive timestamp')
    if value>=Decimal('1e17'): return int(value/1000000),int(value),'ns'
    if value>=Decimal('1e14'): return int(value/1000),None,'us'
    if value>=Decimal('1e11'): return int(value),None,'ms'
    decimals=max(0,-value.as_tuple().exponent)
    precision='ns' if decimals>=9 else 'us' if decimals>3 else 'ms' if decimals else 's'
    return int(value*1000),int(value*1000000000) if precision=='ns' else None,precision

def archive_url(market,symbol,filename):
    if market not in ROOTS or not re.fullmatch(r'[A-Za-z0-9_.-]+',symbol) or '..' in symbol: raise ValueError('Invalid market/symbol')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+\.(csv|csv.gz|zip)',filename) or '..' in filename: raise ValueError('Invalid filename')
    return f'{BASE}/{market}/{quote(symbol)}/{quote(filename)}'

class Links(HTMLParser):
    def __init__(self): super().__init__(); self.links=[]
    def handle_starttag(self,tag,attrs):
        if tag=='a': self.links.extend(v for k,v in attrs if k=='href' and v)

def discover(url=BASE+'/',transport=None):
    t=transport or Transport()
    with t.get(url) as r:
        parser=Links(); parser.feed(r.text)
    base=url.rstrip('/')+'/'
    result=[]
    for href in parser.links:
        target=urljoin(base,href); p=urlsplit(target)
        if p.scheme!='https' or p.netloc!='public.bybit.com' or not target.startswith(base) or target==base or p.query or p.fragment: continue
        tail=unquote(p.path)[len(unquote(urlsplit(base).path)):].rstrip('/')
        if not tail or '/' in tail or '\\' in tail or tail in {'.','..'}: continue
        dates=re.findall(r'(\d{4}-\d{2}-\d{2})',tail)
        start=end=None
        if dates:
            start=dates[0]; end=(date.fromisoformat(dates[-1])+timedelta(days=1)).isoformat()
        else:
            month=re.search(r'(\d{4})-(\d{2})(?=\.)',tail)
            if month:
                y,m=map(int,month.groups()); start=date(y,m,1).isoformat()
                end=date(y+(m==12),m%12+1,1).isoformat()
        result.append({'url':target,'name':tail,'directory':not bool(re.search(r'\.(gz|csv|zip)$',tail)),'date':dates[0] if dates else None,'start_date':start,'end_date_exclusive':end})
    return sorted(result,key=lambda x:x['name'])

def validate(path,source_url=None):
    path=Path(path); digest=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''): digest.update(block)
    with path.open('rb') as f: magic=f.read(2)
    if '.gz' in path.name and magic!=b'\x1f\x8b': raise ValueError('Not gzip')
    opener=gzip.open if magic==b'\x1f\x8b' else open
    with opener(path,'rt',encoding='utf-8-sig',newline='') as f:
        reader=csv.reader(f,strict=True); header=next(reader,None)
        if not header or len(header)<2: raise ValueError('Missing CSV header')
        if any(re.search(r'<\s*!?\s*(?:doctype|html|head|body|script)\b',cell,re.I) for cell in header): raise ValueError('HTML is not a CSV archive')
        header_present=not bool(re.fullmatch(r'\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}',header[0]))
        if not header_present and len(header)!=6: raise ValueError('Invalid MT4 layout')
        columns=header if header_present else ['datetime_utc','open','high','low','close','volume']
        root=urlsplit(source_url or '').path.split('/')[1:2]
        expected={'trading':{'timestamp','price'},'spot':{'timestamp','price'},'premium_index':{'start_at','open','high','low','close'},'spot_index':{'start_at','open','high','low','close'}}.get(root[0] if root else '')
        if expected and not expected.issubset(columns): raise ValueError('Archive CSV does not match source schema')
        time_column=next((columns.index(k) for k in ['timestamp','start_at','datetime_utc'] if k in columns),None)
        count=0; first=last=None
        for row in reader if header_present else chain([header],reader):
            if len(row)!=len(header): raise ValueError('CSV width mismatch')
            if time_column is not None:
                value=row[time_column]
                if columns[time_column]=='datetime_utc': ts=int(datetime.strptime(value,'%Y.%m.%d %H:%M').replace(tzinfo=timezone.utc).timestamp()*1000)
                else:
                    ts,_,_=timestamp_fields(value)
                first=ts if first is None else min(first,ts)
                last=ts if last is None else max(last,ts)
            count+=1
        if not count: raise ValueError('Empty CSV')
    return {'sha256':digest.hexdigest(),'bytes':path.stat().st_size,'rows':count,'columns':columns,'header_present':header_present,'first_timestamp_ms':first,'last_timestamp_ms':last}

def iter_trade_records(path,category,symbol,provenance,allow_composite=False):
    """Stream B v1.0 trade envelopes after whole-file validation.

    Original IDs always win. Explicit fallback is SHA256 of sorted original
    row JSON + category/symbol; identical no-ID rows cannot be distinguished.
    Such fallback must never establish unique-trade completeness.
    """
    if category not in {'spot','linear','inverse'}: raise ValueError('Invalid category')
    checked=validate(path)
    if provenance.get('sha256') and provenance['sha256']!=checked['sha256']: raise ValueError('Provenance hash mismatch')
    if 'timestamp' not in checked['columns'] or not checked['header_present']: raise ValueError('Not a recognized trade archive')
    source={**provenance,**checked,'fetched_at':provenance.get('fetched_at') or utcnow(),'source_kind':'bybit_archive','ingestion_run':provenance.get('ingestion_run') or 'run:'+uuid.uuid4().hex}
    source['artifact_validation']={'kind':'archive_validation','ingestion_run':source['ingestion_run'],'url':source.get('url'),'validation_time':utcnow(),'checked':checked,'storage_uri':str(Path(path).resolve()),'hash_scope':'original_archive_bytes','declared_download_run':provenance.get('ingestion_run')}
    with open(path,'rb') as raw: magic=raw.read(2)
    opener=gzip.open if magic==b'\x1f\x8b' else open
    with opener(path,'rt',encoding='utf-8-sig',newline='') as f:
        for row in csv.DictReader(f):
            if row.get('symbol',symbol)!=symbol: raise ValueError('Archive symbol mismatch')
            timestamp_ms,timestamp_ns,precision=timestamp_fields(row['timestamp'])
            trade_id=row.get('trdMatchID') or row.get('id')
            origin='exchange'
            if not trade_id:
                if not allow_composite: raise ValueError('Missing exchange trade ID; explicit composite opt-in required')
                trade_id=hashlib.sha256(json.dumps([category,symbol,row],sort_keys=True,separators=(',',':')).encode()).hexdigest()
                origin='composite_sha256_raw_row_v1'
            result={'schema_version':'1.0','venue':'bybit','category':category,'symbol':symbol,'dataset':'trades','interval':'','timestamp_ms':timestamp_ms,'timestamp_ns':timestamp_ns,'timestamp_precision':precision,'trade_id':str(trade_id),'id_origin':origin,'payload':row,'source':{**source,'source_timestamp':row['timestamp']},'availability':{'status':'PARTIAL','reason':'Validated archive observations; complete exchange interval inventory not established'}}
            if precision=='us':
                raw=Decimal(row['timestamp'])
                result['timestamp_us']=int(raw) if raw>=Decimal('1e14') else int(raw*1000000)
            yield result

class ArchiveLoader:
    def __init__(self,journal,transport=None): self.journal=journal; self.transport=transport or Transport()

    def download(self,url,destination):
        path=Path(destination); path.parent.mkdir(parents=True,exist_ok=True)
        key='archive:'+url; old=self.journal.task(key)
        if old and old['status']=='validated' and path.exists():
            try:
                checked=validate(path,url)
                if checked['sha256']==old['sha256']:
                    info={**old,**checked,'path':str(path)}
                    info.pop('status',None)
                    self.journal.save(key,'validated',info)
                    return info
            except (ValueError,OSError,EOFError,csv.Error,UnicodeError): pass
        part=path.with_name(path.name+'.partial'); meta=part.with_name(part.name+'.json')
        run_id=self.journal.start_run('archive_download',{'url':url,'path':str(path)})
        self.journal.save(key,'running',{'url':url,'path':str(path)},run_id=run_id)
        try:
            for attempt in range(3):
                headers={'Accept-Encoding':'identity'}; offset=0
                if part.exists() and meta.exists():
                    try: saved=json.loads(meta.read_text())
                    except (ValueError,OSError): saved={}
                    etag=saved.get('etag')
                    if saved.get('url')==url and etag and not etag.startswith('W/') and part.stat().st_size:
                        with self.transport.get(url,headers={'Range':'bytes=0-0','Accept-Encoding':'identity'},stream=True) as probe:
                            total=re.fullmatch(r'bytes 0-0/(\d+)',probe.headers.get('Content-Range',''))
                            if probe.status_code==206 and total and probe.headers.get('ETag')==etag and part.stat().st_size<int(total[1]):
                                offset=part.stat().st_size; headers.update({'Range':f'bytes={offset}-','If-Range':etag})
                try:
                    with self.transport.get(url,headers=headers,stream=True) as r:
                        if offset:
                            cr=re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)',r.headers.get('Content-Range',''))
                            if r.status_code!=206 or not cr or int(cr[1])!=offset or int(cr[2])+1!=int(cr[3]) or r.headers.get('ETag')!=etag:
                                part.unlink(missing_ok=True); meta.unlink(missing_ok=True); continue
                            expected=int(cr[3])
                        else:
                            if r.status_code!=200: raise ValueError('Unexpected partial response')
                            expected=int(r.headers['Content-Length']) if 'Content-Length' in r.headers else None
                        if r.headers.get('Content-Encoding','identity')!='identity': raise ValueError('Unexpected content encoding')
                        meta.write_text(json.dumps({'url':url,'etag':r.headers.get('ETag')}))
                        with part.open('ab' if offset else 'wb') as out:
                            for block in r.iter_content(1024*1024):
                                if block: out.write(block)
                        if expected is not None and part.stat().st_size!=expected: raise ValueError('Incomplete Content-Length')
                        info={**validate(part,url),'url':url,'path':str(path),'fetched_at':utcnow(),'etag':r.headers.get('ETag'),'last_modified':r.headers.get('Last-Modified'),'source_kind':'bybit_archive','ingestion_run':run_id}
                    ref=self.journal.add_evidence({'kind':'archive_download','ingestion_run':run_id,'manifest':info,'http_status':r.status_code,'range_request':headers,'hash_scope':'original_archive_bytes'})
                    info['evidence_refs']=[ref]
                    part.replace(path); self.journal.save(key,'validated',info,run_id=run_id); meta.unlink(missing_ok=True)
                    self.journal.finish_run(run_id,'PARTIAL')
                    return info
                except requests.RequestException:
                    if attempt==2: raise
                except (ValueError,EOFError,OSError,csv.Error,UnicodeError):
                    part.unlink(missing_ok=True); meta.unlink(missing_ok=True)
                    if attempt==2: raise
            raise ValueError('Inconsistent Range responses')
        except Exception as exc:
            self.journal.save(key,'failed',{'url':url,'path':str(path),'error':str(exc)},run_id=run_id)
            self.journal.finish_run(run_id,'FAILED'); raise

    def many(self,files,directory,workers=3):
        if not 1<=workers<=8: raise ValueError('workers must be 1..8')
        def run(item):
            relative=unquote(urlsplit(item['url']).path).lstrip('/')
            if '\\' in relative or ':' in relative or any(x in {'.','..'} for x in relative.split('/')): raise ValueError('Unsafe path')
            return self.download(item['url'],Path(directory)/relative)
        with ThreadPoolExecutor(max_workers=workers) as pool: return list(pool.map(run,files))

def download(url,destination,session=None):
    destination=Path(destination); destination.parent.mkdir(parents=True,exist_ok=True)
    journal=Journal(destination.parent/'archive-jobs.sqlite')
    try: return ArchiveLoader(journal,Transport(session=session)).download(url,destination)
    finally: journal.close()
