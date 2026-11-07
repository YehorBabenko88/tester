"""Half-open UTC intervals; overlapping evidence never inflates coverage."""
STATES={'available','requested','downloaded','verified','unknown','corrupt','unavailable'}

def _append(ranges,a,b):
    if ranges and ranges[-1][1]==a: ranges[-1][1]=b
    else: ranges.append([a,b])

def coverage_report(start,end,intervals):
    if type(start) is not int or type(end) is not int or not 0<=start<end: raise ValueError('invalid requested interval')
    active=[]; points={start,end}
    for row in intervals:
        if row.get('superseded',False): continue
        a,b=max(start,row['start']),min(end,row['end'])
        if row['state'] not in STATES or row['start']>=row['end']: raise ValueError('invalid coverage evidence')
        if a<b: active.append((a,b,row['state'])); points.update((a,b))
    result={s:[] for s in STATES}; points=sorted(points)
    for a,b in zip(points,points[1:]):
        states={s for x,y,s in active if x<=a and y>=b}
        blocking=states & {'corrupt','unavailable','unknown'}
        for state in states-{'verified'}: _append(result[state],a,b)
        if 'verified' in states and not blocking: _append(result['verified'],a,b)
        if not states & {'verified','corrupt','unavailable'} and 'unknown' not in states: _append(result['unknown'],a,b)
    verified=sum(b-a for a,b in result['verified']); available=sum(b-a for a,b in result['available'])
    complete=verified==end-start and available==end-start and not any(result[s] for s in ('unknown','corrupt','unavailable'))
    return result|dict(start=start,end=end,verified_ms=verified,percent=verified*100/(end-start),complete=complete)
