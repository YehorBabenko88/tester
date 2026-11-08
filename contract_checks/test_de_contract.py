"""Independent Issue #8 gates; run explicitly, never ordinary root discovery.

Fixtures describe controlled responses, not a claim that an API exists.
The immutable snapshots are fetched from GitHub; absent implementations fail gates.
"""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

HERE = Path(__file__).parent
D_SHA = '421f22ba3a2c377c16f08b3f0f5b79417078e98f'
E_SHA = '36c939fcfb36ec8b8e7e529f1f1cdda8083a3279'

DOM_PROBE = r'''
const fs = require('node:fs');
const vm = require('node:vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
class Element {
  constructor() { this.children=[]; this.textContent=''; this.value=''; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children=[...nodes]; }
  addEventListener() {}
}
const elements=Object.fromEntries(['#symbols','#filter','#error','#refresh'].map(k=>[k,new Element()]));
const calls=[];
const context={document:{querySelector:k=>elements[k],createElement:()=>new Element()},
  fetch:async url=>{calls.push(url);return {ok:true,json:async()=>input.payload}},console};
vm.createContext(context);
const script=input.html.match(/<script>([\s\S]*?)<\/script>/)[1];
vm.runInContext(script,context);
setImmediate(()=>{
  const text=n=>[n.textContent,...n.children.map(text)].join(' ');
  console.log(JSON.stringify({rendered:Object.values(elements).map(text).join(' '),calls}));
});
'''


class DEContractGates(unittest.TestCase):
    def tree(self, name, sha):
        data = json.loads((HERE / 'snapshots' / (name + '-tree.json')).read_text())
        self.assertEqual(data['sha'], sha)
        self.assertFalse(data['truncated'])
        return {entry['path'] for entry in data['tree'] if entry['type'] == 'blob'}

    def render(self, payload):
        self.assertIsNotNone(shutil.which('node'), 'Node required; cannot silently skip D gate')
        source = (HERE / 'snapshots/d-ui/index.html').read_text(encoding='utf-8')
        result = subprocess.run(['node', '-e', DOM_PROBE], input=json.dumps({'html':source,'payload':payload}),
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_d_snapshot_identity(self):
        self.assertIn('frontend/index.html', self.tree('d', D_SHA))

    def test_d_renders_unavailable_reason_without_analytics(self):
        result = self.render({'instruments':[], 'availability':'UNAVAILABLE',
                              'reason':'archive_missing_fixture', 'schema_version':'1.0.0'})
        self.assertIn('UNAVAILABLE', result['rendered'], 'D hides availability')
        self.assertIn('archive_missing_fixture', result['rendered'], 'D hides reason')
        self.assertEqual(result['calls'], ['/api/instruments?category=linear'])

    def test_d_partial_record_retains_reason_no_invented_metric(self):
        result = self.render({'instruments':[{'symbol':'FIXTUREUSDT','status':'Trading',
                              'availability':'PARTIAL','reason':'missing_taker_side_fixture',
                              'delta':None}], 'schema_version':'1.0.0',
                              'availability':'PARTIAL','reason':'missing_taker_side_fixture'})
        self.assertIn('PARTIAL', result['rendered'], 'Trading status is not data availability')
        self.assertIn('missing_taker_side_fixture', result['rendered'])
        self.assertNotRegex(result['rendered'], r'(?i)(delta|volatility|capitalization)\s*[:=]?\s*0')

    def test_d_no_fallback_analytics_observed(self):
        result = self.render({'instruments':[], 'availability':'UNAVAILABLE', 'reason':'missing_fixture'})
        self.assertNotRegex(result['rendered'], r'(?i)(delta|volatility|capitalization)\s*[:=]?\s*\d')
        self.assertEqual(result['calls'], ['/api/instruments?category=linear'])

    def test_e_api_and_shared_schema_adoption_exist(self):
        paths = self.tree('e', E_SHA)
        api = [p for p in paths if p.startswith('src/api/') and p.endswith('.py')]
        self.assertTrue(api, 'NOT_READY: E snapshot has no API implementation; schema validation cannot run')
        self.assertIn('contracts/v1/market.schema.json', paths, 'Shared contract not adopted by E')

    def test_e_no_duplicated_analytics_requires_api_review(self):
        paths = self.tree('e', E_SHA)
        self.assertTrue(any(p.startswith('src/api/') for p in paths),
                        'NOT_READY: absent API cannot prove delegation to C rather than duplicated analytics')


if __name__ == '__main__':
    print('Issue #8 immutable D=' + D_SHA + ' E=' + E_SHA, flush=True)
    unittest.main(verbosity=2)

