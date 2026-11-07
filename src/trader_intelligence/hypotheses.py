"""C context and F simulation boundary: candidate rules are never observed facts."""
from .imports import digest


def entry_context(event, points):
    data=[]
    for i,p in enumerate(points):
        if i>=10000: raise ValueError('context budget')
        if type(p.get('available_at_ms')) is not int or type(p.get('timestamp_ms')) is not int:
            raise ValueError('context timestamps required')
        if (p.get('venue'),p.get('category'),p.get('symbol'))!=(event['venue'],event['category'],event['symbol']): continue
        if max(p['timestamp_ms'],p['available_at_ms'])>event['timestamp_ms']: continue
        if not p.get('evidence_ref'): raise ValueError('context evidence required')
        data.append(dict(p))
    data.sort(key=lambda p:(p['timestamp_ms'],p['available_at_ms'],p['metric']))
    return {'data':data,'classification':'CONTEXT_ONLY','causal_explanation':'UNKNOWN',
            'unknown':['historical_l2','historical_l3','trader_intent','undisclosed_sl','undisclosed_tp'],
            'warning':'Association does not establish an entry rule; disclosure delay and selection bias remain.'}


def simulation_plan(analysis_run_id,artifact_digest,strategy,snapshot,engine_revision):
    import re
    if not isinstance(engine_revision,str) or not re.fullmatch('[0-9a-f]{40}',engine_revision): raise ValueError('pin F engine commit')
    # Deep copy through canonical JSON, so callers cannot mutate an approved plan by alias.
    from json import loads
    from .imports import canonical
    p=loads(canonical({'analysis_run_id':analysis_run_id,'artifact_digest':artifact_digest,
        'classification':'INFERRED','strategy':strategy,'snapshot':snapshot,
        'engine_revision':engine_revision,'operation':'OFFLINE_SIMULATION'}))
    return dict(p,plan_digest=digest(p))


def simulate(plan,approved_digest,engine):
    base={k:v for k,v in plan.items() if k!='plan_digest'}
    if digest(base)!=plan.get('plan_digest') or approved_digest!=plan['plan_digest']:
        raise PermissionError('exact simulation plan approval required')
    result=engine(plan['strategy'],plan['snapshot'])
    artifact={'classification':'INFERRED','operation':'OFFLINE_SIMULATION',
              'analysis_run_id':plan['analysis_run_id'],'parent_artifact_digest':plan['artifact_digest'],
              'engine_revision':plan['engine_revision'],'plan_digest':plan['plan_digest'],
              'result':result,'warning':'Candidate hypothesis; not the trader actual strategy or realized PnL.'}
    return dict(artifact,artifact_digest=digest(artifact))
