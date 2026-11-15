const {test,expect}=require('@playwright/test');
const {execFileSync}=require('node:child_process');
const {mkdtempSync,readFileSync,writeFileSync,rmSync}=require('node:fs');
const {tmpdir}=require('node:os');
const {join}=require('node:path');
let fixtures;
test.beforeAll(()=>{
  fixtures=mkdtempSync(join(tmpdir(),'compressor-recovery-'));
  for(const [name,codec] of [['small.mp4','libx264'],['av1.mp4','libaom-av1'],['av1.webm','libaom-av1'],['small.mov','libx264']]) {
    execFileSync('ffmpeg',['-v','error','-f','lavfi','-i','testsrc2=size=320x240:rate=30','-t','0.4','-c:v',codec,
      ...(codec==='libaom-av1'?['-cpu-used','8']:[]),join(fixtures,name)]);
  }
  for(const name of ['small.mp4','av1.mp4']){
    const input=readFileSync(join(fixtures,name)),padding=Buffer.alloc(1200000-input.length);
    padding.writeUInt32BE(padding.length);padding.write('free',4);
    writeFileSync(join(fixtures,'large-'+name),Buffer.concat([input,padding]));
  }
});
test.afterAll(()=>rmSync(fixtures,{recursive:true,force:true}));
async function open(page,request,metadata=null){
  await page.addInitScript(metadata=>{
    window.executions=[];
    const send=Worker.prototype.postMessage;
    Worker.prototype.postMessage=function(message,...rest){
      if(message?.type==='EXEC')window.executions.push(message.data.args);
      if(metadata&&message?.type==='READ_FILE'&&message.data.path.startsWith('probe-')){
        queueMicrotask(()=>this.onmessage?.({data:{id:message.id,type:message.type,data:JSON.stringify(metadata)}}));return;
      }
      return send.call(this,message,...rest);
    };
  },metadata);
  const session=await(await request.post('/test/session')).json();
  await page.goto(session.path);return session;
}
for(const file of ['large-av1.mp4','av1.webm'])test(`AV1 ${file} stops without encoding and preserves other clips and caption`,async({page,request})=>{
  await open(page,request);
  await page.locator('#caption').fill('Keep my caption');
  await page.locator('#clips').setInputFiles([join(fixtures,file),join(fixtures,'small.mp4')]);
  await page.locator('#submit').click();
  await expect(page.locator('#message')).toContainText('H.264 MP4 with AAC audio');
  await expect(page.locator('#message')).toContainText('other selected clips');
  expect(await page.evaluate(()=>window.executions.length)).toBe(0);
  await expect(page.locator('#caption')).toHaveValue('Keep my caption');
  await expect(page.locator('.file-card')).toHaveCount(2);
  await expect(page.getByRole('button',{name:'Reselect '+file,exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Remove '+file,exact:true}).click();
  await page.locator('#submit').click();
  await expect(page.locator('#page-heading')).toHaveText('Delivered to Discord');
});
test('fitting AV1 MP4 remains unchanged without decoder use',async({page,request})=>{
  await open(page,request);await page.locator('#clips').setInputFiles(join(fixtures,'av1.mp4'));
  await page.locator('#submit').click();await expect(page.locator('#page-heading')).toHaveText('Delivered to Discord');
  expect(await page.evaluate(()=>window.executions.length)).toBe(0);
});
for(const audio of [true,false])test(`impossible duration budget stops before conversion, audio=${audio}`,async({page,request})=>{
  const streams=[{codec_type:'video',codec_name:'h264',width:320,height:240,avg_frame_rate:'30/1'}];
  if(audio)streams.push({codec_type:'audio',codec_name:'pcm_s16le'});
  await open(page,request,{format:{duration:'5305'},streams});
  // MP4 must encode; MOV with incompatible audio must convert.
  await page.locator('#clips').setInputFiles(join(fixtures,audio?'small.mov':'large-small.mp4'));
  await page.locator('#submit').click();await expect(page.locator('#message')).toContainText('too long to fit');
  expect(await page.evaluate(()=>window.executions.length)).toBe(0);
});
test('long compatible MOV can still remux when output fits',async({page,request})=>{
  await open(page,request,{format:{duration:'5305'},streams:[{codec_type:'video',codec_name:'h264',width:320,height:240,avg_frame_rate:'30/1'}]});
  await page.locator('#clips').setInputFiles(join(fixtures,'small.mov'));await page.locator('#submit').click();
  await expect(page.locator('#page-heading')).toHaveText('Delivered to Discord');
  expect(await page.evaluate(()=>window.executions.every(args=>args.includes('copy')))).toBe(true);
});
test('upload connection reset retries prepared output without re-encoding',async({page,request})=>{
  const session=await open(page,request);let posts=0,encoded;
  await page.route('**'+session.path,async route=>{
    if(route.request().method()!=='POST')return route.continue();
    posts++;
    if(posts===1){encoded=await page.evaluate(()=>window.executions.length);return route.fulfill({status:503,json:{code:'upload_connection_reset',error:'Interrupted',retry_after:1}});}
    return route.continue();
  });
  await page.locator('#clips').setInputFiles(join(fixtures,'large-small.mp4'));await page.locator('#submit').click();
  await expect(page.locator('#page-heading')).toHaveText('Delivered to Discord',{timeout:20000});
  expect(posts).toBe(2);expect(encoded).toBeGreaterThan(0);
  expect(await page.evaluate(()=>window.executions.length)).toBe(encoded);
});
test('exhausted reset retries preserve downloadable output and manual recovery',async({page,request})=>{
  const session=await open(page,request);let posts=0;
  await page.route('**'+session.path,route=>{
    if(route.request().method()!=='POST')return route.continue();posts++;
    return route.fulfill({status:503,json:{code:'upload_connection_reset',error:'Interrupted',retry_after:1}});
  });
  await page.locator('#clips').setInputFiles(join(fixtures,'small.mp4'));await page.locator('#submit').click();
  await expect(page.locator('#page-heading')).toHaveText('Delivery needs attention',{timeout:20000});
  expect(posts).toBe(3);
  await expect(page.locator('#message')).toContainText('do not need to be compressed again');
  await expect(page.locator('#downloads a')).toHaveCount(1);
  await expect(page.locator('#downloads a')).toHaveAttribute('href',/^blob:/);
  await page.unroute('**'+session.path);await page.locator('#submit').click();
  await expect(page.locator('#page-heading')).toHaveText('Delivered to Discord');
});
test('lost upload response checks receipt without duplicating delivery',async({page,request})=>{
  const session=await open(page,request);let posts=0;
  await page.route('**'+session.path,async route=>{
    if(route.request().method()!=='POST')return route.continue();posts++;
    await route.fetch();await route.abort('connectionreset');
  });
  await page.locator('#clips').setInputFiles(join(fixtures,'small.mp4'));await page.locator('#submit').click();
  await expect(page.locator('#page-heading')).toHaveText('Delivered to Discord',{timeout:20000});
  expect(posts).toBe(1);
});
test('AV1 unavailable acceleration signal is classified without retaining raw logs',async({page,request})=>{
  await open(page,request);
  const signal=await page.evaluate(async()=>{
    const module=await import('/brand/diagnostics.js');
    return module.engineSignal("[av1 @ 0x123] Your platform doesn't suppport hardware accelerated AV1 decoding.");
  });expect(signal).toBe('unsupported_codec');
});

for(const audio of [true,false])test(`budget reserves audio only when present, audio=${audio}`,async({page,request})=>{
  const streams=[{codec_type:'video',codec_name:'h264',width:320,height:240,avg_frame_rate:'30/1'}];
  if(audio)streams.push({codec_type:'audio',codec_name:'aac'});
  await open(page,request,{format:{duration:'70'},streams});
  await page.locator('#clips').setInputFiles(join(fixtures,'large-small.mp4'));await page.locator('#submit').click();
  if(audio){await expect(page.locator('#message')).toContainText('too long to fit');expect(await page.evaluate(()=>window.executions.length)).toBe(0);}
  else{await expect(page.locator('#page-heading')).toHaveText('Delivered to Discord');expect(await page.evaluate(()=>window.executions.length)).toBe(1);}
});
test('repeated submit events cannot send the same batch twice',async({page,request})=>{
  const session=await open(page,request);let posts=0;
  await page.route('**'+session.path,async route=>{
    if(route.request().method()==='POST'){posts++;await new Promise(resolve=>setTimeout(resolve,100));}
    return route.continue();
  });
  await page.locator('#clips').setInputFiles(join(fixtures,'small.mp4'));
  await expect(page.locator('#submit')).toBeEnabled();
  await page.evaluate(()=>{document.querySelector('#upload').requestSubmit();document.querySelector('#upload').requestSubmit();});
  await expect(page.locator('#page-heading')).toHaveText('Delivered to Discord');expect(posts).toBe(1);
});
test('expired delivery retry retains local downloads without resending',async({page,request})=>{
  const session=await open(page,request);let posts=0;
  await page.route('**'+session.path,route=>{
    if(route.request().method()!=='POST')return route.continue();posts++;
    return route.fulfill({status:503,json:{code:'upload_connection_reset',error:'Interrupted',retry_after:1}});
  });
  await page.locator('#clips').setInputFiles(join(fixtures,'small.mp4'));await page.locator('#submit').click();
  await expect(page.locator('#page-heading')).toHaveText('Delivery needs attention',{timeout:20000});
  await page.route('**'+session.path+'/status',route=>route.fulfill({status:410,json:{code:'session_expired',error:'Expired'}}));
  await page.locator('#submit').click();await expect(page.locator('#message')).toContainText('session expired');
  await expect(page.locator('#downloads a')).toHaveCount(1);await expect(page.locator('#submit')).toBeHidden();expect(posts).toBe(3);
});
