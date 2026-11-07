"""python -m src.ingest: no database credentials or trading keys required."""
import argparse
import json
from pathlib import Path
from .archive import discover,ArchiveLoader,BASE
from .journal import Journal
from .rest import API,Backfill,SPECS
from .catalog import instruments,screener

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--journal',default='ingest-jobs.sqlite')
    sub=p.add_subparsers(dest='command',required=True)
    d=sub.add_parser('discover'); d.add_argument('url',nargs='?',default=BASE+'/')
    a=sub.add_parser('archive'); a.add_argument('url'); a.add_argument('destination')
    a=sub.add_parser('archive-range'); a.add_argument('url'); a.add_argument('directory'); a.add_argument('--start',required=True); a.add_argument('--end',required=True); a.add_argument('--workers',type=int,default=3)
    r=sub.add_parser('rest'); r.add_argument('category',choices=['spot','linear','inverse']); r.add_argument('symbol'); r.add_argument('kind',choices=SPECS); r.add_argument('--interval',default='1'); r.add_argument('--start-ms',type=int,required=True); r.add_argument('--end-ms',type=int,required=True); r.add_argument('--limit',type=int); r.add_argument('--export',default='handoff.json',help='Self-contained JSON handoff, including raw records and evidence (not JSONL)')
    c=sub.add_parser('catalog'); c.add_argument('category',choices=['spot','linear','inverse']); c.add_argument('--status')
    s=sub.add_parser('screener'); s.add_argument('category',choices=['spot','linear','inverse']); s.add_argument('symbol')
    args=p.parse_args(); api=API()
    if args.command=='discover': result=discover(args.url)
    elif args.command=='catalog': result=list(instruments(api,args.category,args.status))
    else:
        with_journal=Path(args.journal); with_journal.parent.mkdir(parents=True,exist_ok=True)
        j=Journal(with_journal)
        try:
            if args.command=='archive': result=ArchiveLoader(j).download(args.url,args.destination)
            elif args.command=='archive-range':
                files=[f for f in discover(args.url) if not f['directory'] and f['start_date'] and f['start_date']<args.end and f['end_date_exclusive']>args.start]
                result=ArchiveLoader(j).many(files,args.directory,args.workers)
            elif args.command=='screener': result=screener(api,j,args.category,args.symbol)
            else:
                result=Backfill(api,j).run(args.category,args.symbol,args.kind,args.interval,args.start_ms,args.end_ms,limit=args.limit)
                j.export(args.export)
        finally: j.close()
    print(json.dumps(result,indent=2))

if __name__=='__main__': main()
