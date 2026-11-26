"""A18: direct import of prepared extension to exact source blob; no PostgreSQL/network."""
import hashlib
import json
import secrets
import shutil
from pathlib import Path
from xml.etree import ElementTree as ET
import pytest
from scripts import ci_named_junit_gate as g

HEAD='1270015bdbcaf2208bb419a45be87d118141e3c0'
NODES=g.CANONICAL_DATA_RESEARCH_NODES
FAKE=tuple(f'tests/unrelated/test_fake.py::test_spoof_{i}' for i in range(5))

def rawxml(path, nodes=NODES, fail_index=None):
    root=ET.Element('testsuites');s=ET.SubElement(root,'testsuite',tests=str(len(nodes)),skipped='0',failures=str(1 if fail_index is not None else 0),errors='0')
    for i,n in enumerate(nodes):
        cls,name=g.expected_case_identity(n)
        case=ET.SubElement(s,'testcase',classname=cls,name=name)
        if i==fail_index:ET.SubElement(case,'failure',message='RAW SECRET DISPOSABLE')
    ET.ElementTree(root).write(path)

def receipt(tmp_path, ordinal, nodes=NODES, *, attempt=None, suite=None, rc=0, timed_out=False, raw_exists=True, fail_index=None):
    attempt=attempt or secrets.token_hex(16)
    raw=tmp_path/f'raw-{ordinal}.xml'
    if raw_exists:rawxml(raw,nodes,fail_index)
    target=tmp_path/f'pg16-mandatory-{ordinal}.xml'
    try:
        g.safe_artifact(raw,target,nodes,head_sha=HEAD,postgres_major='16',
           suite_name=suite or f'data-research-pg16-repeat{ordinal}',
           process_returncode=rc,timed_out=timed_out,attempt_id=attempt,
           repeat_ordinal=ordinal)
    except ValueError:pass
    return target,g.validate_receipt(target)

def reserve(tmp_path,attempts):
    p=tmp_path/'pg16-two-repetitions.json'
    g.initialize_repetition_manifest(p,head_sha=HEAD,postgres_major='16',expected_repetitions=2)
    for i,attempt in enumerate(attempts,1):
        g.begin_repetition_attempt(p,head_sha=HEAD,postgres_major='16',
            repeat_ordinal=i,attempt_id=attempt)
    return p

@pytest.mark.parametrize('kind,expected',[
 ('missing','UNKNOWN'),('timeout','UNKNOWN'),('rc1','UNKNOWN'),('not_started','NOT_EXECUTED')
])
def test_no_unverified_named_pass(tmp_path,kind,expected):
    overrides={'raw_exists':False} if kind=='missing' else {'timed_out':True,'rc':None} if kind=='timeout' else {'rc':1} if kind=='rc1' else {'rc':None}
    _,q=receipt(tmp_path,1,**overrides)
    assert q['status']=='NOT_VERIFIED'
    assert [r[2] for r in q['observed'][:5]]==[expected]*5
    assert q['observed'][-1][-1]=='FAIL'
    assert all(r[2]!='PASS' for r in q['observed'])

def test_actual_junit_failure_marked_fail_but_rest_unknown(tmp_path):
    target,q=receipt(tmp_path,1,rc=1,fail_index=2)
    assert q['status']=='NOT_VERIFIED'
    assert [r[2] for r in q['observed'][:5]]==['UNKNOWN','UNKNOWN','FAIL','UNKNOWN','UNKNOWN']
    assert 'RAW SECRET' not in target.read_text()

def test_foreign_five_rejected_by_final_manifest(tmp_path):
    ids=[secrets.token_hex(16),secrets.token_hex(16)]
    m=reserve(tmp_path,ids)
    a,_=receipt(tmp_path,1,FAKE,attempt=ids[0]);b,_=receipt(tmp_path,2,FAKE,attempt=ids[1])
    with pytest.raises(ValueError,match='canonical'):
        g.complete_repetition_manifest(m,[a,b],head_sha=HEAD,postgres_major='16')
    assert json.loads(m.read_text())['status']=='NOT_VERIFIED'

def test_copied_report_under_new_name_rejected(tmp_path):
    ids=[secrets.token_hex(16),secrets.token_hex(16)]
    m=reserve(tmp_path,ids)
    a,_=receipt(tmp_path,1,attempt=ids[0]);b=tmp_path/'pg16-mandatory-2.xml'
    shutil.copyfile(a,b);shutil.copyfile(a.with_suffix('.json'),b.with_suffix('.json'))
    with pytest.raises(ValueError,match='identity'):
        g.complete_repetition_manifest(m,[a,b],head_sha=HEAD,postgres_major='16')

def test_swapped_attempts_rejected(tmp_path):
    ids=[secrets.token_hex(16),secrets.token_hex(16)]
    m=reserve(tmp_path,ids)
    a,_=receipt(tmp_path,1,attempt=ids[1]);b,_=receipt(tmp_path,2,attempt=ids[0])
    with pytest.raises(ValueError,match='identity'):
        g.complete_repetition_manifest(m,[a,b],head_sha=HEAD,postgres_major='16')

def test_duplicate_attempt_id_cannot_be_reserved(tmp_path):
    q=secrets.token_hex(16);m=tmp_path/'pg16-two-repetitions.json'
    g.initialize_repetition_manifest(m,head_sha=HEAD,postgres_major='16',expected_repetitions=2)
    g.begin_repetition_attempt(m,head_sha=HEAD,postgres_major='16',repeat_ordinal=1,attempt_id=q)
    with pytest.raises(ValueError,match='reservation'):
        g.begin_repetition_attempt(m,head_sha=HEAD,postgres_major='16',repeat_ordinal=2,attempt_id=q)

def test_two_real_attempt_ids_same_results_accepted(tmp_path):
    ids=[secrets.token_hex(16),secrets.token_hex(16)]
    m=reserve(tmp_path,ids)
    a,qa=receipt(tmp_path,1,attempt=ids[0]);b,qb=receipt(tmp_path,2,attempt=ids[1])
    assert [x[:2] for x in qa['observed']]==[x[:2] for x in qb['observed']]
    g.complete_repetition_manifest(m,[a,b],head_sha=HEAD,postgres_major='16')
    aggregate=json.loads(m.read_text())
    assert aggregate['status']=='PASS'
    assert [x['attempt_id'] for x in aggregate['repetitions']]==ids

def test_same_raw_xml_bytes_not_rejected_by_sha_gate(tmp_path):
    ids=[secrets.token_hex(16),secrets.token_hex(16)]
    m=reserve(tmp_path,ids)
    a,qa=receipt(tmp_path,1,attempt=ids[0]);b,qb=receipt(tmp_path,2,attempt=ids[1])
    assert hashlib.sha256((tmp_path/'raw-1.xml').read_bytes()).hexdigest()==hashlib.sha256((tmp_path/'raw-2.xml').read_bytes()).hexdigest()
    g.complete_repetition_manifest(m,[a,b],head_sha=HEAD,postgres_major='16')
    assert json.loads(m.read_text())['status']=='PASS'

def test_tampered_xml_hash_rejected(tmp_path):
    a,_=receipt(tmp_path,1)
    a.write_bytes(a.read_bytes()+b'\n')
    with pytest.raises(ValueError,match='SHA256'):
        g.validate_receipt(a)

def test_tampered_receipt_attempt_rejected(tmp_path):
    a,q=receipt(tmp_path,1)
    q['attempt_id']=secrets.token_hex(16)
    a.with_suffix('.json').write_text(json.dumps(q))
    with pytest.raises(ValueError,match='Attempt ID'):
        g.validate_receipt(a)

def test_missing_attempt_reservation_rejected(tmp_path):
    m=tmp_path/'pg16-two-repetitions.json';g.initialize_repetition_manifest(m,head_sha=HEAD,postgres_major='16',expected_repetitions=2)
    a,_=receipt(tmp_path,1);b,_=receipt(tmp_path,2)
    with pytest.raises(ValueError,match='aggregate'):
        g.complete_repetition_manifest(m,[a,b],head_sha=HEAD,postgres_major='16')

def test_noncanon_inventory_equal_size_not_accepted(tmp_path):
    assert len(NODES)==5
    assert g.CANONICAL_DATA_RESEARCH_NODES != FAKE


def test_round18_real_pytest_generated_junit_accepted(tmp_path):
    """Genuine pytest XML validates under the exact-source-derived gate, no DB."""
    import subprocess
    import sys
    cases_dir=tmp_path/'tests'/'offline'
    cases_dir.mkdir(parents=True)
    (cases_dir/'test_cases.py').write_text(
        'import unittest\n'
        'def test_plain(): assert 3 == 3\n'
        'class Smoke(unittest.TestCase):\n'
        '    def test_method(self): self.assertTrue(True)\n',encoding='utf-8')
    raw=tmp_path/'actual.xml'
    p=subprocess.run([sys.executable,'-m','pytest','-q','-o','junit_family=xunit2',
           f'--junitxml={raw}','tests/offline/test_cases.py'],cwd=tmp_path,
           capture_output=True,text=True,timeout=30)
    assert p.returncode==0, p.stdout[-900:]
    nodes=('tests/offline/test_cases.py::test_plain',
           'tests/offline/test_cases.py::Smoke::test_method')
    ids=g.check_junit(ET.parse(raw).getroot(),nodes)
    assert len(ids)==2
    result=g.safe_artifact(raw,tmp_path/'real.xml',nodes,head_sha=HEAD,
             postgres_major='18',suite_name='positive-real-pytest',
             process_returncode=p.returncode,timed_out=False,
             attempt_id=secrets.token_hex(16))
    assert result['status']=='PASS'

@pytest.mark.parametrize('kind,expected', [
    ('missing','UNKNOWN'), ('timeout','UNKNOWN'), ('rc1','UNKNOWN'),
    ('not_started','NOT_EXECUTED'),
])
def test_round20_standard_skipped_for_unverified_case(tmp_path,kind,expected):
    overrides = {'raw_exists':False} if kind == 'missing' else ({'rc':None,'timed_out':True} if kind == 'timeout' else {'rc':1} if kind == 'rc1' else {'rc':None})
    target, result = receipt(tmp_path,1,**overrides)
    assert result['status'] == 'NOT_VERIFIED'
    xml = ET.parse(target).getroot()
    suite = xml[0]
    for aggregate in (xml,suite):
        assert aggregate.get('tests') == '6'
        assert aggregate.get('skipped') == '5'
        assert aggregate.get('failures') == '1'
        assert aggregate.get('errors') == '0'
    for case in list(suite)[:5]:
        assert case.get('status') == expected
        assert len(case) == 1 and case[0].tag == 'skipped'
        assert case[0].get('message') == 'mandatory evidence not verified'
    assert suite[-1].get('status') == 'FAIL' and suite[-1][0].tag == 'failure'
    assert g.validate_receipt(target) == result
    # A conventional element-based JUnit outcome reader sees these as SKIP, not PASS.
    assert ['SKIP' if c.find('skipped') is not None else 'PASS' for c in list(suite)[:5]] == ['SKIP']*5

@pytest.mark.parametrize('mutation', ['removed_skipped','extra_skipped','skipped_count','skipped_message','status_changed','extra_child'])
def test_round20_reject_tampered_standard_junit_markers(tmp_path,mutation):
    target,_ = receipt(tmp_path,1,rc=1)
    root=ET.parse(target).getroot()
    suite=root[0]; case=suite[0]
    if mutation=='removed_skipped': case.remove(case[0])
    elif mutation=='extra_skipped': ET.SubElement(case,'skipped',message='mandatory evidence not verified')
    elif mutation=='skipped_count': suite.set('skipped','0')
    elif mutation=='skipped_message': case[0].set('message','injected')
    elif mutation=='status_changed': case.set('status','PASS')
    elif mutation=='extra_child': ET.SubElement(case[0],'error')
    target.write_bytes(ET.tostring(root,encoding='utf-8',xml_declaration=True))
    data=json.loads(target.with_suffix('.json').read_text())
    data['sanitized_xml_sha256']=hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix('.json').write_text(json.dumps(data))
    with pytest.raises(ValueError):g.validate_receipt(target)

def test_round20_raw_skipped_is_never_mandatory_pass(tmp_path):
    raw=tmp_path/'raw.xml'
    rawxml(raw)
    tree=ET.parse(raw)
    s=tree.getroot()[0];s.set('skipped','1')
    ET.SubElement(s[0],'skipped',message='skipped by pytest')
    tree.write(raw)
    target=tmp_path/'mandatory.xml'
    with pytest.raises(ValueError):
        g.safe_artifact(raw,target,NODES,head_sha=HEAD,postgres_major='16',
            suite_name='guarded-raw-skip',process_returncode=0,timed_out=False)
    q=g.validate_receipt(target)
    assert q['status']=='NOT_VERIFIED'
    assert q['observed'][-1][-1]=='FAIL'
    assert all(c[-1]!='PASS' for c in q['observed'])
