"""Pure deterministic bar-event simulation. No IO and no exchange dependencies."""
import copy
from decimal import Decimal, localcontext, ROUND_FLOOR
from src.strategy.dsl import validate_strategy, evaluate, decimal, digest
from .quality import validate_snapshot
from .metrics import scorecard

ENGINE_VERSION='strategy-bar-1.1.0'
ZERO=Decimal(0)


def wire(x):
    if isinstance(x,Decimal): return str(x)
    if isinstance(x,dict): return {k:wire(v) for k,v in x.items()}
    if isinstance(x,list): return [wire(v) for v in x]
    return x


class Backtest:
    def __init__(self,strategy,snapshot):
        with localcontext() as ctx:
            ctx.prec=50
            self.strategy=validate_strategy(strategy)
            self.snapshot,self.warnings,self.gaps=validate_snapshot(snapshot,self.strategy)
        self.strategy_hash=digest(self.strategy); self.market_hash=digest(self.snapshot)
        self.input_hash=digest({'strategy':self.strategy_hash,'market':self.market_hash,'engine':ENGINE_VERSION})
        self.index=0; self.cash=decimal(self.snapshot['initial_equity']); self.position=None; self.pending=None
        self.orders=[]; self.fills=[]; self.signals=[]; self.trades=[]; self.brackets=[]
        self.curve=[{'timestamp_ms':self.snapshot['start_ms'],'cash':str(self.cash),'equity':str(self.cash),'margin':'0'}]
        self.funding_costs=ZERO; self.exposure_ms=0; self.history=[]; self.capacity=None

    def _order(self,side,role,reason,kind='market',trigger=None):
        o={'id':f'o{len(self.orders)+1}','side':side,'role':role,'reason':reason,'type':kind,
           'trigger_price':trigger,'created_at_ms':self.history[-1]['end_ms'],
           'eligible_index':self.index+self.strategy['order']['latency_bars'], 'status':'pending'}
        self.orders.append(o); self.pending=o
        return o

    def _price(self,price,buy,limit=None):
        costs=self.strategy['costs']
        friction=(decimal(costs['slippage_bps'])+decimal(costs['spread_bps'])/2)/10000
        p=price*(1+friction if buy else 1-friction)
        # A limit is a hard cap even under modeled transaction friction.
        return min(p,limit) if limit is not None and buy else max(p,limit) if limit is not None else p

    def _fill(self,o,b,price,qty,intrabar=False):
        fee=qty*price*decimal(self.strategy['costs']['fee_rate'])
        # A whole-bar capacity is known at close, even when its price reference is
        # the open. Never publish that quantity as a known, exact opening fill.
        uncertain=intrabar or self.capacity is not None
        f={'id':f'f{len(self.fills)+1}','order_id':o['id'],'side':o['side'],'role':o['role'],
           'timestamp_ms':b['end_ms']-1 if uncertain else b['start_ms'],
           'known_at_ms':b['end_ms'] if uncertain else b['start_ms'],
           'time_window':{'start_ms':b['start_ms'],'end_ms':b['end_ms']},
           'time_precision':'bar' if uncertain else 'modeled_open',
           'liquidity_evidence_refs':b['liquidity']['evidence_refs'] if self.capacity is not None else [],
           'quantity':qty,'price':price,'fee':fee,'reason':o['reason']}
        self.fills.append(f); self.cash-=fee
        if self.capacity is not None: self.capacity-=qty
        return f

    def _cap(self,qty):
        return min(qty,self.capacity) if self.capacity is not None else qty

    def _enter(self,o,b):
        side=o['side']; sign=Decimal(1) if side=='long' else Decimal(-1)
        op=decimal(b['open']); hi=decimal(b['high']); lo=decimal(b['low']); level=o['trigger_price']
        intrabar=False; price=op
        if o['type']=='limit':
            if (side=='long' and lo>level) or (side=='short' and hi<level): return
            price=min(op,level) if side=='long' else max(op,level); intrabar=price!=op
        elif o['type']=='stop':
            if (side=='long' and hi<level) or (side=='short' and lo>level): return
            price=max(op,level) if side=='long' else min(op,level); intrabar=price!=op
        price=self._price(price,side=='long',level if o['type']=='limit' else None)
        r=self.strategy['risk']; leverage=decimal(r['leverage']); fee=decimal(self.strategy['costs']['fee_rate'])
        size=r['size']; qty=decimal(size['value'])
        if size['mode']=='equity_fraction': qty=self.cash*qty/(price*(1/leverage+fee))
        step=decimal(self.snapshot['quantity_step']); qty=(qty/step).to_integral_value(rounding=ROUND_FLOOR)*step
        if qty<=0 or qty*price*(1/leverage+fee)>self.cash:
            o['status']='rejected_margin'; self.pending=None; return
        requested=qty; qty=self._cap(qty)
        qty=(qty/step).to_integral_value(rounding=ROUND_FLOOR)*step
        if qty<=0:
            o['status']='cancelled_no_liquidity'; self.pending=None; return
        f=self._fill(o,b,price,qty,intrabar)
        o['status']='filled' if qty==requested else 'partial_cancelled'; o['requested_quantity']=requested; o['filled_quantity']=qty
        self.position={'side':side,'sign':sign,'quantity':qty,'entry_price':price,'entry_fill_id':f['id'],
                       'entry_timestamp_ms':f['timestamp_ms'],'entry_fee':f['fee'],'funding':ZERO,
                       'stop':price*(1-sign*decimal(r['stop_loss'])),
                       'target':price*(1+sign*decimal(r['take_profit'])), 'trail':None}
        self.pending=None

    def _exit(self,b,price,reason,intrabar=False,order=None):
        p=self.position
        if order is None:
            if self.pending is not None and self.pending['role']=='exit' and self.pending['reason']==reason:
                order=self.pending
            else:
                if self.pending is not None: self.pending['status']='partial_cancelled' if self.pending['status']=='partially_filled' else 'cancelled_protection'
                order={'id':f'o{len(self.orders)+1}','side':p['side'],'role':'exit','type':'market',
                       'reason':reason,'created_at_ms':b['end_ms']-1 if intrabar else b['start_ms'],
                       'eligible_index':self.index,'status':'pending','triggered':True,
                       'trigger_window':{'start_ms':b['start_ms'],'end_ms':b['end_ms']},
                       'trigger_known_at_ms':b['end_ms'] if intrabar else b['start_ms']}
                self.orders.append(order)
        if 'requested_quantity' not in order: order['requested_quantity']=p['quantity']
        order.setdefault('filled_quantity',ZERO)
        qty=self._cap(p['quantity'])
        step=decimal(self.snapshot['quantity_step']); qty=(qty/step).to_integral_value(rounding=ROUND_FLOOR)*step
        if qty<=0:
            # Triggering is an order-state transition even with no executable size.
            order['eligible_index']=self.index+1; self.pending=order
            return
        order['filled_quantity']=order.get('filled_quantity',ZERO)+qty
        price=self._price(price,p['side']=='short')
        f=self._fill(order,b,price,qty,intrabar)
        weight=qty/p['quantity']; entry_fee=p['entry_fee']*weight; funding=p['funding']*weight
        gross=p['sign']*qty*(price-p['entry_price']); self.cash+=gross
        self.trades.append({'id':f't{len(self.trades)+1}','side':p['side'],'quantity':qty,
            'entry_fill_id':p['entry_fill_id'],'exit_fill_id':f['id'],
            'entry_timestamp_ms':p['entry_timestamp_ms'],'exit_timestamp_ms':f['timestamp_ms'],
            'entry_price':p['entry_price'],'exit_price':price,'stop_loss':p['stop'],'take_profit':p['target'],
            'gross_pnl':gross,'net_pnl':gross-entry_fee-f['fee']-funding,
            'fees':entry_fee+f['fee'],'funding_costs':funding,'reason':reason})
        p['quantity']-=qty; p['entry_fee']-=entry_fee; p['funding']-=funding
        order['status']='filled' if not p['quantity'] else 'partially_filled'
        if not p['quantity']:
            self.position=None
            if self.pending is not None and self.pending is not order: self.pending['status']='cancelled_protection'
            self.pending=None
        else:
            order['eligible_index']=self.index+1; self.pending=order

    def _protect(self,b,entry_intrabar=False,open_only=False):
        if not self.position: return
        # An already-triggered reduce-only market residual cannot be untriggered by
        # a rebound or reclassified as another bracket. It awaits eligible evidence.
        if self.pending is not None and self.pending.get('triggered'): return
        p=self.position; sign=p['sign']; op=decimal(b['open']); lo=decimal(b['low']); hi=decimal(b['high'])
        if open_only: lo=hi=op
        stop=p['stop']; reason='stop_loss'
        if p['trail'] is not None and (p['trail']>stop if sign>0 else p['trail']<stop): stop=p['trail']; reason='trailing_stop'
        # Cross-wallet linear maintenance threshold; no insurance fund/ADL model.
        if self.snapshot['category']=='linear':
            q=p['quantity']; e=p['entry_price']; m=decimal(self.strategy['risk']['maintenance_margin'])
            liq=(q*e-self.cash)/(q*(1-m)) if sign>0 else (self.cash+q*e)/(q*(1+m))
            if liq>0 and (liq>stop if sign>0 else liq<stop): stop=liq; reason='liquidation'
        stop_hit=lo<=stop if sign>0 else hi>=stop
        target_hit=hi>=p['target'] if sign>0 else lo<=p['target']
        # Adverse path wins ambiguity. Intrabar entries never receive same-bar targets.
        if stop_hit:
            px=min(op,stop) if sign>0 else max(op,stop)
            # Pre-entry open cannot price an intrabar stop; use triggered stop.
            if entry_intrabar: px=stop
            self._exit(b,px,reason,intrabar=entry_intrabar or px!=op)
        elif target_hit and not entry_intrabar:
            # No favorable gap improvement assumed for resting target.
            self._exit(b,p['target'],'take_profit',intrabar=not open_only)

    def step(self):
        if self.index>=len(self.snapshot['candles']): return False
        with localcontext() as ctx:
            ctx.prec=50
            self._step()
        return True

    def _step(self):
        b=self.snapshot['candles'][self.index]
        gap=(self.history[-1]['end_ms'] if self.history else self.snapshot['start_ms']) != b['start_ms']
        if gap:
            if self.pending and not self.pending.get('triggered'):
                self.pending['status']='cancelled_gap'; self.pending=None
            self.history=[]  # reset all signal windows; do not fabricate candles
        participation=self.strategy['order'].get('max_participation')
        if participation is not None and 'liquidity' not in b: raise ValueError('partial fills require verified liquidity evidence on every eligible bar')
        self.capacity=decimal(b['liquidity']['quantity'])*decimal(participation) if participation is not None else None
        held=bool(self.position)
        if self.strategy['costs']['funding_policy']=='required':
            for ev in self.snapshot['funding']['events']:
                if ev['timestamp_ms']==b['start_ms'] and self.position:
                    p=self.position; cost=p['sign']*p['quantity']*decimal(ev['mark_price'])*decimal(ev['rate'])
                    self.cash-=cost; self.funding_costs+=cost; p['funding']+=cost
        entered_intrabar=False
        # Only open-price evidence is known at this execution phase.
        if self.position: self._protect(b,open_only=True)
        if self.pending and self.pending['eligible_index']<=self.index:
            o=self.pending
            if o['role']=='entry' and not self.position:
                before=len(self.fills); self._enter(o,b)
                if len(self.fills)>before:
                    entered_intrabar=self.fills[-1]['time_precision']=='bar'
                    held=True
            elif o['role']=='exit' and self.position:
                self._exit(b,decimal(b['open']),o['reason'],order=o)
        if self.position: self._protect(b,entered_intrabar)
        if held and not self.position and self.fills and self.fills[-1]['timestamp_ms']==b['start_ms']:
            held=False
        if held: self.exposure_ms+=b['end_ms']-b['start_ms']
        self.history.append(b)
        if self.position:
            p=self.position
            self.brackets.append({'timestamp_ms':b['end_ms'],'entry_fill_id':p['entry_fill_id'],
                                  'stop_loss':p['stop'],'take_profit':p['target'],'trailing_stop':p['trail']})
            trail=decimal(self.strategy['risk']['trailing_stop'])
            if trail:
                candidate=decimal(b['close'])*(1-p['sign']*trail)
                p['trail']=candidate if p['trail'] is None else max(candidate,p['trail']) if p['sign']>0 else min(candidate,p['trail'])
            if self.pending is None and evaluate(self.strategy['exit'],self.history):
                self.signals.append({'timestamp_ms':b['end_ms'],'side':p['side'],'action':'exit','reason':'exit_condition'})
                self._order(p['side'],'exit','exit_condition')
        elif self.pending is None and self.cash>0:
            sides=[side for side,expr in self.strategy['entry'].items() if evaluate(expr,self.history)]
            if len(sides)>1:
                self.signals.append({'timestamp_ms':b['end_ms'],'action':'conflict','reason':'simultaneous_long_short'})
            elif sides:
                side=sides[0]; o=self.strategy['order']; sign=1 if side=='long' else -1
                offset=decimal(o.get('offset_bps','0'))/10000
                trigger=decimal(b['close'])*(1+sign*offset*(1 if o['type']=='stop' else -1)) if o['type']!='market' else None
                self.signals.append({'timestamp_ms':b['end_ms'],'side':side,'action':'entry','reason':'entry_condition'})
                self._order(side,'entry','entry_condition',o['type'],trigger)
        unreal=ZERO; margin=ZERO
        if self.position:
            p=self.position; unreal=p['sign']*p['quantity']*(decimal(b['close'])-p['entry_price'])
            margin=p['quantity']*p['entry_price']/decimal(self.strategy['risk']['leverage'])
        self.curve.append({'timestamp_ms':b['end_ms'],'cash':str(self.cash),'equity':str(self.cash+unreal),'margin':str(margin)})
        self.index+=1
        if self.index==len(self.snapshot['candles']) and self.pending:
            self.pending['status']='cancelled_end'; self.pending=None

    def _prefix(self):
        return digest(wire({'index':self.index,'cash':self.cash,'position':self.position,'pending':self.pending,
                            'orders':self.orders,'fills':self.fills,'signals':self.signals,'trades':self.trades,'curve':self.curve}))

    def checkpoint(self):
        return {'schema_version':'1.0','engine_version':ENGINE_VERSION,'input_hash':self.input_hash,
                'next_index':self.index,'prefix_digest':self._prefix()}

    def result(self):
        with localcontext() as ctx:
            ctx.prec=50
            markers=[{'id':f'm-{f["id"]}','fill_id':f['id'],'order_id':f['order_id'],
                      'timestamp_ms':f['timestamp_ms'],'known_at_ms':f['known_at_ms'],
                      'price':f['price'],'quantity':f['quantity'],
                      'side':f['side'],'role':f['role'],'reason':f['reason'],
                      'time_window':f['time_window'],'time_precision':f['time_precision']} for f in self.fills]
            return wire({'schema_version':'1.0','run_id':self.input_hash,'engine_version':ENGINE_VERSION,
                'strategy_id':self.strategy['id'],'strategy_version':self.strategy['version'],
                'strategy_hash':self.strategy_hash,'market_snapshot_hash':self.market_hash,
                'parameters':self.strategy,'sources':self.snapshot['provenance'],
                'data_quality':{'availability':self.snapshot['availability'],'coverage':self.snapshot['coverage'],'gaps':self.gaps},
                'warnings':self.warnings,'status':'completed' if self.index==len(self.snapshot['candles']) else 'paused',
                'processed_bars':self.index,'signals':self.signals,'orders':self.orders,'fills':self.fills,
                'trades':self.trades,'open_position':self.position,'overlays':markers,'brackets':self.brackets,
                'equity_curve':self.curve,'metrics':scorecard(wire(self.trades),self.curve,wire(self.fills),self.funding_costs,
                    self.exposure_ms,self.curve[-1]['timestamp_ms']-self.snapshot['start_ms']),
                'replay_digest':self._prefix()})


def run(strategy,snapshot):
    b=Backtest(strategy,snapshot)
    while b.step(): pass
    return b.result()


def restore(strategy,snapshot,checkpoint):
    b=Backtest(strategy,snapshot)
    if checkpoint.get('engine_version')!=ENGINE_VERSION or checkpoint.get('input_hash')!=b.input_hash:
        raise ValueError('checkpoint input/version mismatch')
    n=checkpoint.get('next_index')
    if type(n) is not int or not 0<=n<=len(b.snapshot['candles']): raise ValueError('checkpoint index')
    for _ in range(n): b.step()
    if checkpoint.get('prefix_digest')!=b._prefix(): raise ValueError('checkpoint replay mismatch')
    return b
