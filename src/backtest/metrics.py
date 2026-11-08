from decimal import Decimal


def scorecard(trades, curve, fills, funding_costs, exposure_ms, duration_ms):
    pnl=[Decimal(t['net_pnl']) for t in trades]
    wins=sum((p for p in pnl if p>0),Decimal(0)); losses=-sum((p for p in pnl if p<0),Decimal(0))
    count=len(pnl); peak=Decimal(curve[0]['equity']); max_dd=Decimal(0)
    for point in curve:
        e=Decimal(point['equity']); peak=max(peak,e)
        dd=(peak-e)/peak if peak>0 else Decimal(0)
        max_dd=max(max_dd,dd)
    return {'trade_count':count,'win_rate':str(Decimal(sum(p>0 for p in pnl))/count) if count else None,
            'profit_factor':str(wins/losses) if losses else None,
            'expectancy':str(sum(pnl)/count) if count else None,
            'net_realized_pnl':str(sum(pnl,Decimal(0))), 'max_drawdown':str(max_dd),
            'exposure':str(Decimal(exposure_ms)/duration_ms) if duration_ms else '0',
            'fees':str(sum((Decimal(f['fee']) for f in fills),Decimal(0))),
            'funding_costs':str(funding_costs),
            'confidence':{'method':'not_estimated','reason':'dependent, selected historical trades; no IID confidence claim'}}
