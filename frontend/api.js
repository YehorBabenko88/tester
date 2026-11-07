export async function request(path,params={},options={}) {
 const timeout=AbortSignal.timeout(20000);
 const signal=options.signal?AbortSignal.any([options.signal,timeout]):timeout;
 const query=new URLSearchParams(Object.entries(params).filter(([,v])=>v!=null));
 const response=await (options.fetcher||fetch)(path+(query.size?'?'+query:''),{
  signal,method:options.method||'GET',headers:{Accept:'application/json',...(options.body?{'Content-Type':'application/json'}:{})},
  ...(options.body?{body:JSON.stringify(options.body)}:{})
 });
 const declared=Number(response.headers.get('content-length'));
 if(declared>8_000_000) throw Error('Response exceeds 8 MB safety limit');
 const reader=response.body?.getReader(); let raw='';
 if(reader){let size=0; const decoder=new TextDecoder();while(true){const {done,value}=await reader.read();if(done)break;size+=value.length;if(size>8_000_000){await reader.cancel();throw Error('Response exceeds 8 MB safety limit');}raw+=decoder.decode(value,{stream:true});}raw+=decoder.decode();}
 else raw=await response.text();
 let payload;try{payload=JSON.parse(raw);}catch{throw Error('Invalid JSON response');}
 if(!response.ok) {const e=payload.error||payload.detail||{};throw Error(`${response.status} ${e.code||'API_ERROR'}: ${e.message||String(e)}`);}
 if(payload.schema_version!=='1.0'||!Object.hasOwn(payload,'data')) throw Error('Unsupported API schema');
 return payload;
}
