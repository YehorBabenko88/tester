"""Conservative model reconstruction; never promote inferred inventory to observed."""
from collections import defaultdict
from decimal import Decimal, localcontext
from .core import normalize


def reconstruct(rows, manifest):
    unknown={'status':'UNKNOWN','positions':[], 'gross_realized_pnl':None,
             'net_realized_pnl':None,'win_rate':None,'profit_factor':None,
             'expectancy':None,'drawdown':None,'fees':None,'funding':None}
    if not (manifest['flat_start'] and manifest['coverage']=='COMPLETE' and
            manifest['position_mode']=='ONE_WAY' and manifest['instrument_model']=='LINEAR'):
        return dict(unknown,reason='complete flat-start linear one-way history required')
    groups=defaultdict(list)
    for r in rows:
        e=normalize(r)
        if e.kind!='DISCLOSED_EXECUTION' or e.side=='UNKNOWN':
            return dict(unknown,reason='only identified disclosed execution sides can be reconciled')
        groups[(e.source,e.trader_id,e.venue,e.category,e.symbol)].append(e)
    if not groups: return dict(unknown,reason='no executions')
    positions=[]; total=Decimal(0); closes=[]; cycles=[]
    # 128-character decimals can include exponents; core bounds exponent/scale.
    with localcontext() as ctx:
        ctx.prec=512
        for key,events in sorted(groups.items()):
            events.sort(key=lambda e:e.timestamp_ms)
            if len({e.timestamp_ms for e in events})!=len(events):
                return dict(unknown,reason='same-timestamp execution order unavailable')
            quantity=Decimal(0); cost=Decimal(0); legs=[]; cycle_pnl=Decimal(0); opened=None
            for e in events:
                size=Decimal(e.quantity)*(1 if e.side=='BUY' else -1); price=Decimal(e.price)
                if not quantity: opened=e.timestamp_ms
                if not quantity or quantity*size>0:
                    cost=(abs(quantity)*cost+abs(size)*price)/(abs(quantity)+abs(size))
                else:
                    closed=min(abs(quantity),abs(size))
                    pnl=closed*(price-cost)*(1 if quantity>0 else -1)
                    total+=pnl
                    cycle_pnl+=pnl
                    closes.append({'event_id':e.event_id,'quantity':str(closed),
                                   'gross_pnl':str(pnl),'classification':'INFERRED'})
                    if abs(size)>=abs(quantity):
                        cycles.append({'gross_pnl':str(cycle_pnl),'holding_ms':e.timestamp_ms-opened})
                        cycle_pnl=Decimal(0); opened=e.timestamp_ms
                    if abs(size)>abs(quantity): cost=price
                quantity+=size
                if quantity==0: cost=Decimal(0)
                legs.append(e.event_id)
            positions.append(dict(zip(('source','trader_id','venue','category','symbol'),key),
                                  quantity=str(quantity),average_entry=str(cost) if quantity else None,
                                  classification='INFERRED',evidence_event_ids=legs,sl=None,tp=None))
        stats=None
        if len(groups)==1:
            pnls=[Decimal(c['gross_pnl']) for c in cycles]
            gains=sum((p for p in pnls if p>0),Decimal(0)); losses=-sum((p for p in pnls if p<0),Decimal(0))
            stats={'classification':'INFERRED','completed_cycles':len(cycles),
                   'gross_win_rate':str(Decimal(sum(p>0 for p in pnls))/len(pnls)) if pnls else None,
                   'gross_profit_factor':str(gains/losses) if losses else None,
                   'gross_expectancy':str(sum(pnls)/len(pnls)) if pnls else None,
                   'mean_holding_ms':str(Decimal(sum(c['holding_ms'] for c in cycles))/len(cycles)) if cycles else None}
    # Never sum instruments with unknown currency/contract multipliers.
    return dict(unknown,status='INFERRED',reason='conditional weighted-average model; completeness is exporter-attested',
                positions=positions,gross_realized_pnl=str(total) if len(groups)==1 else None,partial_closes=closes,
                model_statistics=stats,model_assumptions=['quantity in base units','unit linear multiplier','no transfers or liquidations'],
                confidence='CONDITIONAL_ON_EXPORT_ATTESTATION')
