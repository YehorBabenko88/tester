"""A deliberately small, auditable text grammar; everything else needs clarification."""
import copy
import re
from .dsl import preview_text, validate_strategy


def compile_text(text,base_strategy):
    """Parse 'long/short when FIELD OP NUMBER' using explicit base risk/cost settings.

    An external language model may instead call preview_text with a full candidate
    and assumptions; neither route authorizes execution without preview confirmation.
    """
    base=validate_strategy(base_strategy)
    match=re.fullmatch(r'\s*(long|short)\s+when\s+(open|high|low|close|volume)\s*(>=|<=|>|<|==)\s*(-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?)\s*',text,re.IGNORECASE)
    if not match:
        return preview_text(text,None,['Unresolved entry rule. Supply an exact comparison or a reviewed JSON candidate.'])
    side,field,op,value=match.groups(); candidate=copy.deepcopy(base)
    candidate['entry']={side.lower():{'op':{'>':'gt','>=':'gte','<':'lt','<=':'lte','==':'eq'}[op],
                                    'left':{'field':field.lower()},'right':{'value':value}}}
    return preview_text(text,candidate,[
        'Only the entry condition is supplied by this text.',
        'Exit, stop/target/trailing, size, leverage, costs, data policies and execution latency use the displayed base configuration.',
        'Evaluate closed bars; earliest execution is following eligible evidence.'])
