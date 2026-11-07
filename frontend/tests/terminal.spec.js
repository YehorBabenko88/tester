import {test,expect} from '@playwright/test';
import {readFile} from 'node:fs/promises';
import path from 'node:path';
const start=1704067200000;
const m=value=>({value,source:'Bybit REST',as_of:start,availability:'AVAILABLE',unit:'USD'});
const instruments=[{category:'linear',symbol:'BTCUSDT',metrics:{price:m('42000.01'),volatility:m('2.4')}},{category:'linear',symbol:'ETHUSDT',metrics:{price:m('2200.1'),volatility:m('3.5')}}];
async function setup(page,options={}){
 await page.route('http://terminal.test/**',async route=>{
  const u=new URL(route.request().url());let data={};
  if(!u.pathname.startsWith('/api/')){const file=path.join(process.cwd(),u.pathname==='/'?'index.html':u.pathname.slice(1));await route.fulfill({body:await readFile(file),contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});return;}
  if(u.pathname==='/api/sources')data=[{id:'bybit_rest',availability:'available',kinds:['candles','trades'],provenance:'Bybit'}];
  if(u.pathname==='/api/instruments')data=options.instruments||{instruments:instruments.filter(x=>x.symbol.includes((u.searchParams.get('search')||'').toUpperCase()))};
  if(u.pathname==='/api/downloads') data=route.request().method()==='POST'?{id:'j1',state:'DISCOVERING'}:{jobs:options.jobs||[]};
  if(u.pathname.startsWith('/api/downloads/')){if(options.onJob)await options.onJob();data=options.jobs?.[0]||{id:'j1',state:'DOWNLOADING'};}
  if(u.pathname==='/api/quality')data={availability:'PARTIAL',reason:'Archive not verified',gaps:[{start_ms:start,end_ms:start+60000}],raw:true};
  if(['/api/candles','/api/footprint','/api/open-interest','/api/funding','/api/analytics'].includes(u.pathname))data={availability:'DATA_UNAVAILABLE',reason:'Historical trades not ingested',source:'C aggregates',as_of:start,rows:[]};
  if(u.pathname==='/api/candles'&&options.candles)data=options.candles;
  if(u.pathname==='/api/footprint'&&options.footprint)data=options.footprint;
  if(u.pathname==='/api/open-interest'&&options.oi)data=options.oi;
  if(u.pathname==='/api/funding'&&options.funding)data=options.funding;
  if(u.pathname==='/api/analytics'&&options.analytics)data=options.analytics(u.searchParams.get('metric'));
  if(u.pathname==='/api/coverage')data=[{status:'partial',kind:'candles',start_ms:start,end_ms:start+60000,provenance:'Archive not verified'}];
  if(options.apiError&&u.pathname==='/api/instruments'){await route.fulfill({status:503,json:{error:{code:'provider_unavailable',message:'DB offline'}}});return;}
  await route.fulfill({json:{schema_version:'1.0',data,next_cursor:null}});
 });
 if(options.jobs)await page.addInitScript(()=>localStorage.setItem('bybit-jobs','["j1"]'));
 await page.goto('/');
}
test('multi-selection survives search and reload, saves exact identities',async({page})=>{
 await setup(page);await page.getByLabel('Select BTCUSDT').check();await page.getByLabel('Select ETHUSDT').check();
 await page.getByLabel('Search symbols').fill('BTC');await expect(page.getByTestId('selection-count')).toHaveText('2 selected');
 await page.getByRole('button',{name:'Save watchlist'}).click();await page.reload();await expect(page.getByTestId('selection-count')).toHaveText('2 selected');
});
test('partial candles show provenance, closure and synchronized time ranges',async({page})=>{
 await setup(page,{candles:{availability:'PARTIAL',source:'Bybit archive',as_of:start,completeness:'PARTIAL',reason:'Missing interval',rows:[{ts:start,open:'1',high:'3',low:'1',close:'2',volume:'5',is_closed:false}]}});
 await page.getByLabel('Range start UTC').fill('2024-01-01T00:00');await page.getByLabel('Range end UTC').fill('2024-01-02T00:00');await page.getByRole('button',{name:'Chart BTCUSDT'}).click();
 await expect(page.locator('[data-panel="candles"]')).toContainText('unclosed 1');await expect(page.locator('[data-panel="volume"]')).toContainText('PARTIAL');
 const requests=[];page.on('request',r=>{if(r.url().includes('/api/'))requests.push(new URL(r.url()));});await page.getByLabel('Zoom in',{exact:true}).click();await expect.poll(()=>requests.filter(u=>u.pathname==='/api/candles').length).toBeGreaterThan(0);
 const rr=requests.filter(u=>['/api/candles','/api/open-interest','/api/footprint','/api/funding','/api/analytics'].includes(u.pathname));expect(new Set(rr.map(u=>u.searchParams.get('start_ms'))).size).toBe(1);expect(new Set(rr.map(u=>u.searchParams.get('end_ms'))).size).toBe(1);expect(rr.every(u=>u.searchParams.get('limit')==='1000')).toBeTruthy();expect(requests.some(u=>u.pathname==='/api/trades')).toBeFalsy();
});
test('large screener virtualizes rows',async({page})=>{
 await setup(page,{instruments:Array.from({length:1000},(_,i)=>({category:'linear',symbol:'COIN'+i,metrics:{}}))});await expect(page.locator('[data-row]')).toHaveCount(20);const initial=await page.locator('[data-row]').first().getAttribute('data-row');await page.locator('#table-scroll').evaluate(e=>e.scrollTop=20000);await expect.poll(()=>page.locator('[data-row]').first().getAttribute('data-row')).not.toBe(initial);expect(await page.locator('[data-row]').count()).toBeLessThan(25);
});
test('saved-only filter resets deep virtual scroll',async({page})=>{
 await setup(page,{instruments:Array.from({length:1000},(_,i)=>({category:'linear',symbol:'COIN'+i,metrics:{}}))});
 const first=page.locator('[data-row]').first();const label=await first.getAttribute('data-row');await first.locator('input').check();await page.getByRole('button',{name:'Save watchlist'}).click();
 await page.locator('#table-scroll').evaluate(e=>e.scrollTop=20000);await expect.poll(()=>page.locator('[data-row]').first().getAttribute('data-row')).not.toBe(label);
 await page.getByLabel('Saved only').check();await expect(page.locator('[data-row]')).toHaveCount(1);
});
test('slow job requests never create overlapping polling waves',async({page})=>{
 let calls=0,release;const wait=new Promise(resolve=>release=resolve);await page.clock.install();
 await setup(page,{jobs:[{id:'j1',state:'DOWNLOADING',progress:0}],onJob:async()=>{calls++;await wait;}});
 await expect.poll(()=>calls).toBe(1);await page.clock.runFor(8000);expect(calls).toBe(1);release();
});
test('polls do not start for a job while retry is pending',async({page})=>{
 await page.clock.install();await setup(page,{jobs:[{id:'j1',state:'FAILED',progress:0}]});
 await expect(page.getByRole('button',{name:'Retry j1'})).toBeEnabled();let gets=0,release;
 const pending=new Promise(resolve=>release=resolve);
 await page.route('**/api/downloads/j1/retry',async route=>{await pending;await route.fulfill({json:{schema_version:'1.0',data:{id:'j1',state:'DOWNLOADING',progress:0}}});});
 await page.route('**/api/downloads/j1',async route=>{gets++;await route.fulfill({json:{schema_version:'1.0',data:{id:'j1',state:'FAILED',progress:0}}});});
 await page.getByRole('button',{name:'Retry j1'}).click();await page.getByRole('button',{name:'Refresh jobs'}).click();await page.clock.runFor(3000);expect(gets).toBe(0);release();
});
test('download creates per-instrument jobs with exclusive UTC range',async({page})=>{
 await setup(page);await page.getByLabel('Select BTCUSDT').check();await page.getByLabel('Select ETHUSDT').check();await page.getByLabel('Range start UTC').fill('2024-01-01T00:00');await page.getByLabel('Range end UTC').fill('2024-01-02T00:00');const bodies=[];page.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname==='/api/downloads')bodies.push(r.postDataJSON());});await page.getByRole('button',{name:'Download History'}).click();await expect.poll(()=>bodies.length).toBe(2);expect(bodies.map(b=>b.symbol).sort()).toEqual(['BTCUSDT','ETHUSDT']);expect(bodies.every(b=>b.start_ms===start&&b.end_ms===start+86400000)).toBeTruthy();
});
test('all derived panels render server strings, profile accepts many prices at one time',async({page})=>{
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await setup(page,{footprint:[{timestamp_ms:start,price:'2',buy_volume:'3',sell_volume:'1',provenance:'C from verified trades'}],analytics:kind=>({availability:'PARTIAL',source:'C',reason:'Coverage partial',rows:kind==='profile'?[{ts:start,price:'1',volume:'3',is_poc:true},{ts:start,price:'2',volume:'2'}]:[{ts:start,value:'1.00000000000000000001'}]})});
 await page.getByLabel('Range start UTC').fill('2024-01-01T00:00');await page.getByLabel('Range end UTC').fill('2024-01-02T00:00');await page.getByRole('button',{name:'Chart BTCUSDT'}).click();
 for(const kind of ['footprint','delta','cvd','profile','poc','vwap','mark','index'])await expect(page.locator(`[data-panel="${kind}"] canvas`)).toBeVisible();
 expect(errors).toEqual([]);
});
test('sort is requested on server and unavailable panels explain why',async({page})=>{
 await setup(page);const req=page.waitForRequest(r=>r.url().includes('/api/instruments')&&r.url().includes('sort=volatility'));
 await page.getByLabel('Sort metric').selectOption('volatility');await req;
 await page.getByRole('button',{name:'Chart BTCUSDT'}).click();
 await expect(page.locator('[data-panel="footprint"]')).toContainText('DATA_UNAVAILABLE');
 await expect(page.locator('[data-panel="footprint"]')).toContainText('Historical trades not ingested');
});
test('API outage is visible and retry works without fake rows',async({page})=>{
 await setup(page,{apiError:true});await expect(page.getByTestId('screener-status')).toContainText('provider_unavailable');
 await expect(page.getByRole('button',{name:'Retry instruments'})).toBeVisible();await expect(page.locator('[data-row]')).toHaveCount(0);
});
test('unknown progress and per-source partial failure support retry',async({page})=>{
 await setup(page,{jobs:[{id:'j1',symbol:'BTCUSDT',state:'PARTIAL',progress:{completed:10},sources:[{source:'archive',state:'FAILED',error:'Checksum mismatch',bytes:1024,verified_records:0}]}]});
 await expect(page.locator('#jobs')).toContainText('Checksum mismatch');await expect(page.locator('#jobs')).toContainText('Unknown total');await expect(page.locator('#jobs')).not.toContainText('100%');
 const req=page.waitForRequest('**/api/downloads/j1/retry');await page.getByRole('button',{name:'Retry j1'}).click();await req;
});
test('corrupt candles are explicit and long range stays bounded',async({page})=>{
 await setup(page,{candles:{availability:'AVAILABLE',source:'Bybit',as_of:start,rows:[{ts:start,open:'9',high:'2',low:'1',close:'2'}]}});
 await page.getByLabel('Range start UTC').fill('2024-01-01T00:00');await page.getByLabel('Range end UTC').fill('2025-01-01T00:00');
 const req=page.waitForRequest(r=>r.url().includes('/api/candles')&&r.url().includes('limit=1000'));
 await page.getByRole('button',{name:'Chart BTCUSDT'}).click();await req;
 await expect(page.locator('[data-panel="candles"]')).toContainText('DATA_UNAVAILABLE');await expect(page.locator('[data-panel="candles"]')).toContainText('Corrupt');
});
test('desktop and narrow layouts contain page content without horizontal overflow',async({page})=>{
 await page.setViewportSize({width:1440,height:1000});await setup(page);
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
 if(process.env.UI_SCREENSHOT){await page.locator('.eyebrow').evaluate(e=>e.textContent='TEST FIXTURE · NOT LIVE MARKET DATA');await page.screenshot({path:process.env.UI_SCREENSHOT,fullPage:true});}
 await page.setViewportSize({width:650,height:900});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
});
test('E v1 raw arrays render OHLCV, OI and funding without claiming complete coverage',async({page})=>{
 await setup(page,{candles:[{timestamp_ms:start,open:'1',high:'3',low:'1',close:'2',volume:'5',provenance:'Bybit REST'}],oi:[{timestamp_ms:start,value:'123.45',provenance:'Bybit REST'}],funding:[{timestamp_ms:start,rate:'0.0001',provenance:'Bybit REST'}]});
 await page.getByLabel('Range start UTC').fill('2024-01-01T00:00');await page.getByLabel('Range end UTC').fill('2024-01-02T00:00');await page.getByRole('button',{name:'Chart BTCUSDT'}).click();
 for(const kind of ['candles','volume','open_interest','funding']){await expect(page.locator(`[data-panel="${kind}"] canvas`)).toBeVisible();await expect(page.locator(`[data-panel="${kind}"]`)).toContainText('PARTIAL');}
 await expect(page.locator('[data-panel="candles"]')).toContainText('closure unknown 1');
});

