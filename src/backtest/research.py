"""Bounded deterministic parameter search and chronologically isolated evaluation."""
import copy
import itertools
from decimal import Decimal, localcontext
from src.strategy.dsl import digest, integer, validate_strategy
from .engine import run

DISCLOSURE='Historical performance does not predict future returns. Parameter selection may overfit.'


def split_snapshot(snapshot,start,stop):
    n=len(snapshot['candles']); integer(start,0,n-1); integer(stop,start+1,n)
    s=copy.deepcopy(snapshot); s['candles']=s['candles'][start:stop]
    s['start_ms']=s['candles'][0]['start_ms']; s['end_ms']=s['candles'][-1]['end_ms']
    if s['funding'] is not None:
        f=s['funding']; f['expected_timestamps']=[t for t in f['expected_timestamps'] if s['start_ms']<=t<s['end_ms']]
        f['events']=[e for e in f['events'] if s['start_ms']<=e['timestamp_ms']<s['end_ms']]
    return s


def _candidates(strategy,grid,max_candidates):
    validate_strategy(strategy); integer(max_candidates,1,4096)
    if not isinstance(grid,dict) or not grid: raise ValueError('nonempty parameter grid required')
    paths=sorted(grid); total=1
    for path in paths:
        if not isinstance(path,str) or not path.startswith(('risk.','costs.','order.','entry.','exit.')): raise ValueError('parameter path')
        node=strategy
        for key in path.split('.'):
            if not isinstance(node,dict) or key not in node: raise ValueError('unknown parameter path')
            node=node[key]
        if isinstance(node,(dict,list)): raise ValueError('only existing scalar parameters can vary')
        values=grid[path]
        if not isinstance(values,list) or not values: raise ValueError('grid values required')
        total*=len(values)
        if total>max_candidates: raise ValueError('grid budget exceeded')
    configs=[]; seen=set()
    for values in itertools.product(*(grid[p] for p in paths)):
        c=copy.deepcopy(strategy)
        for path,value in zip(paths,values):
            node=c; parts=path.split('.')
            for key in parts[:-1]: node=node[key]
            node[parts[-1]]=value
        c=validate_strategy(c); h=digest(c)
        if h not in seen: configs.append(c); seen.add(h)
    return sorted(configs,key=digest)


def _return(result):
    with localcontext() as ctx:
        ctx.prec=50
        initial=Decimal(result['equity_curve'][0]['equity'])
        return (Decimal(result['equity_curve'][-1]['equity'])-initial)/initial


def compare(results):
    return sorted([{'run_id':r['run_id'],'strategy_hash':r['strategy_hash'],
                    'return':str(_return(r)),'metrics':copy.deepcopy(r['metrics'])} for r in results],
                  key=lambda x:(-Decimal(x['return']),x['strategy_hash']))


def grid_search(strategy,snapshot,grid,seed=0,max_candidates=256):
    integer(seed,0,2**32-1)
    results=[run(c,snapshot) for c in _candidates(strategy,grid,max_candidates)]
    return {'schema_version':'1.0','seed':seed,'selection':'in_sample_only',
            'experiment_id':digest({'strategy':strategy,'snapshot':snapshot,'grid':grid,'seed':seed}),
            'candidates':compare(results),'disclosure':DISCLOSURE}


def optimize(strategy,snapshot,grid,train_bars,validation_bars,seed=0,max_candidates=256,shortlist=3):
    n=len(snapshot['candles']); integer(train_bars,1,n-2); integer(validation_bars,1,n-train_bars-1)
    integer(seed,0,2**32-1);integer(shortlist,1,max_candidates)
    train=split_snapshot(snapshot,0,train_bars)
    validation=split_snapshot(snapshot,train_bars,train_bars+validation_bars)
    test=split_snapshot(snapshot,train_bars+validation_bars,n)
    candidates=_candidates(strategy,grid,max_candidates)
    trained=sorted([(c,run(c,train)) for c in candidates],key=lambda p:(-_return(p[1]),digest(p[0])))
    validated=sorted([(c,run(c,validation)) for c,_ in trained[:shortlist]],key=lambda p:(-_return(p[1]),digest(p[0])))
    winner=validated[0][0]
    # Test data is evaluated only once, after immutable validation selection.
    oos=run(winner,test)
    return {'schema_version':'1.0','seed':seed,
            'experiment_id':digest({'strategy':strategy,'snapshot':snapshot,'grid':grid,'seed':seed,
                                    'train_bars':train_bars,'validation_bars':validation_bars,'shortlist':shortlist}),
            'segments':{k:[s['start_ms'],s['end_ms']] for k,s in [('train',train),('validation',validation),('test',test)]},
            'candidate_count':len(candidates),'shortlist_count':len(validated),
            'train':compare([r for _,r in trained]),'validation':compare([r for _,r in validated]),
            'winner_strategy_hash':digest(winner),'winner_strategy':winner,'out_of_sample':oos,
            'stability':{'validation_return_range':[str(min(_return(r) for _,r in validated)),str(max(_return(r) for _,r in validated))],
                         'warning':'Small samples and repeated selection do not establish statistical significance.'},
            'disclosure':DISCLOSURE}


def walk_forward(strategy,snapshot,grid,train_bars,validation_bars,test_bars,seed=0,max_candidates=256):
    for n in (train_bars,validation_bars,test_bars): integer(n,1,len(snapshot['candles']))
    width=train_bars+validation_bars+test_bars; folds=[]
    for start in range(0,len(snapshot['candles'])-width+1,test_bars):
        folds.append(optimize(strategy,split_snapshot(snapshot,start,start+width),grid,
                              train_bars,validation_bars,seed,max_candidates))
    if not folds: raise ValueError('insufficient history for walk-forward')
    returns=[_return(f['out_of_sample']) for f in folds]
    return {'schema_version':'1.0','seed':seed,'folds':folds,
            'stability':{'positive_test_folds':sum(r>0 for r in returns),'test_folds':len(folds),
                         'worst_test_return':str(min(returns)),'best_test_return':str(max(returns)),
                         'selected_strategy_count':len({f['winner_strategy_hash'] for f in folds})},
            'disclosure':DISCLOSURE}
