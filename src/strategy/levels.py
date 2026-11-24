"""Point-in-time support/resistance lifecycle with explicit confirmation delays."""
import calendar
import copy
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from .dsl import decimal, integer


def six_month_start(timestamp_ms):
    dt=datetime.fromtimestamp(timestamp_ms/1000,timezone.utc)
    month=dt.year*12+dt.month-1-6; year,month=divmod(month,12); month+=1
    return int(dt.replace(year=year,month=month,day=min(dt.day,calendar.monthrange(year,month)[1])).timestamp()*1000)


class LevelTracker:
    def __init__(self,left=3,right=3,tolerance_bps='10',min_touches=2,break_mode='close',
                 confirmation_bars=1,retest_bars=10,window_days=None):
        for x in (left,right,min_touches,confirmation_bars,retest_bars): integer(x,1,10000)
        if break_mode not in ('close','wick'): raise ValueError('break mode')
        if window_days is not None: integer(window_days,1,3650)
        if not 0<=decimal(tolerance_bps)<10000: raise ValueError('tolerance')
        self.left=left;self.right=right;self.tolerance_bps=tolerance_bps;self.min_touches=min_touches
        self.break_mode=break_mode;self.confirmation_bars=confirmation_bars;self.retest_bars=retest_bars
        self.window_days=window_days; self.bars=[];self.levels=[];self.events=[]

    def active(self):
        return copy.deepcopy([l for l in self.levels if l['status']=='unbroken' and l['touch_count']>=self.min_touches])

    def push(self,bar):
        with localcontext() as ctx:
            ctx.prec=50
            return self._push(bar)

    def _push(self,bar):
        b=copy.deepcopy(bar)
        integer(b['start_ms'],0,9007199254740991); integer(b['end_ms'],b['start_ms']+1,9007199254740991)
        for k in ('open','high','low','close'): decimal(b[k])
        if self.bars and b['start_ms']<self.bars[-1]['end_ms']: raise ValueError('ordered distinct bars required')
        if self.bars and b['start_ms']!=self.bars[-1]['end_ms']:
            # Missing price evidence invalidates unbroken claims and pivot windows.
            for l in self.levels: l['status']='gap_invalidated'
            self.bars=[]
        self.bars.append(b); now=b['end_ms']; index=len(self.bars)-1; new=[]

        def emit(kind,l):
            e={'kind':kind,'level_id':l['id'],'price':l['price'],'pivot_ms':l['pivot_ms'],
               'known_at_ms':now,'touch_count':l['touch_count']}
            new.append(e)

        cutoff=now-self.window_days*86400000 if self.window_days else six_month_start(now)
        for l in self.levels:
            if l['status'] in ('expired','gap_invalidated','false_breakout'): continue
            if l['pivot_ms']<cutoff:
                l['status']='expired'; emit('expired',l); continue
            p=decimal(l['price']); tol=p*decimal(self.tolerance_bps)/10000; resistance=l['side']=='resistance'
            close=decimal(b['close']); high=decimal(b['high']); low=decimal(b['low'])
            beyond=(high if self.break_mode=='wick' else close)>p+tol if resistance else (low if self.break_mode=='wick' else close)<p-tol
            touch=low<=p+tol and high>=p-tol
            if l['status']=='broken':
                age=index-l['break_index']
                if age>self.retest_bars: continue
                back=close<p-tol if resistance else close>p+tol
                if back:
                    l['status']='false_breakout'; emit('false_breakout',l)
                elif touch and not l['retested']:
                    l['retested']=True; emit('retest',l)
                continue
            l['break_count']=l['break_count']+1 if beyond else 0
            if l['break_count']>=self.confirmation_bars:
                l['status']='broken'; l['break_index']=index; l['retested']=False
                emit('breakout',l); emit('invalidated',l); continue
            if touch and not beyond:
                l['touch_count']+=1
                emit('touch',l)
                if l['touch_count']==self.min_touches: emit('confirmed',l)
        candidate=index-self.right
        if candidate>=self.left:
            center=self.bars[candidate]
            neighbors=self.bars[candidate-self.left:candidate]+self.bars[candidate+1:index+1]
            for field,side,kind in (('high','resistance','pivot_high'),('low','support','pivot_low')):
                p=decimal(center[field]); values=[decimal(z[field]) for z in neighbors]
                strict=all(p>v for v in values) if field=='high' else all(p<v for v in values)
                if strict and center['start_ms']>=cutoff:
                    # Each pivot remains a distinct level; no retrospective clustering.
                    l={'id':f'L{len(self.levels)+1}','side':side,'price':str(p),
                       'pivot_ms':center['start_ms'],'known_at_ms':now,'touch_count':1,
                       'status':'unbroken','break_count':0}
                    self.levels.append(l);emit(kind,l)
                    if self.min_touches==1: emit('confirmed',l)
        self.events.extend(new)
        return copy.deepcopy(new)

    def features(self):
        """Convenience numeric features for the caller's C/F adapter; no provenance fabricated."""
        levels=self.active()
        return {'support':max((decimal(l['price']) for l in levels if l['side']=='support'),default=None),
                'resistance':min((decimal(l['price']) for l in levels if l['side']=='resistance'),default=None)}
