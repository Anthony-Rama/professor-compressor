// Opt-in only: real 4K/8K HEVC decoding can consume substantial browser memory.
// RUN_ENCODER_STRESS=1 npm run test:browser -- encoder-stress.spec.cjs
const {test,expect}=require('@playwright/test');
const {execFileSync}=require('node:child_process');
const {mkdtempSync,readFileSync,writeFileSync,rmSync,existsSync,statSync,readdirSync}=require('node:fs');
const {tmpdir}=require('node:os');
const {join}=require('node:path');
test.skip(process.env.RUN_ENCODER_STRESS!=='1','Explicit opt-in required for high-memory encoding tests');
let fixtures;
const long=process.env.RUN_LONG_ENCODER_STRESS==='1';
const samples=long ? [
  ['8k-8bit-10s','7680x4320','yuv420p',10],['8k-10bit-10s','7680x4320','yuv420p10le',10],
  ['8k-8bit-43s','7680x4320','yuv420p',43],['8k-10bit-43s','7680x4320','yuv420p10le',43]
] : [['4k-8bit','3840x2160','yuv420p',0.4],['8k-8bit','7680x4320','yuv420p',0.4],['8k-10bit','7680x4320','yuv420p10le',0.4]];
test.beforeAll(()=>{
  test.setTimeout(300000);
  fixtures=process.env.ENCODER_STRESS_FIXTURE_DIR||mkdtempSync(join(tmpdir(),'compressor-hevc-stress-'));
  for(const [name,size,pixels,duration] of samples){
    const file=join(fixtures,name+'.mp4');
    if(existsSync(file))continue;
    const seed=long?join(fixtures,pixels+'-seed.mp4'):file;
    if(!existsSync(seed))execFileSync('ffmpeg',['-hide_banner','-loglevel','error','-f','lavfi','-i',
      `testsrc2=size=${size}:rate=30`,'-f','lavfi','-i','sine=frequency=440:sample_rate=48000',
      '-t',String(long?3:duration),'-c:v','libx265','-preset','ultrafast','-pix_fmt',pixels,
      '-x265-params','log-level=error:pools=2:frame-threads=1','-c:a','aac',seed],{timeout:180000});
    // Repeat a moving three-second source: exercises sustained decoding but
    // is not a claim to reproduce the original user's content or HEVC profile.
    if(long)execFileSync('ffmpeg',['-hide_banner','-loglevel','error','-stream_loop','-1','-i',seed,
      '-t',String(duration),'-c','copy',file],{timeout:30000});
    const input=readFileSync(file);
    // A valid free box forces the production MP4 encode path above the
    // harness's 1.1 MB limit, without needing a large/long source video.
    // For 43s tests, match the incident's byte footprint using a free box.
    // This padding stresses file copying, not compressed picture complexity.
    const minimum=long?(duration===43?298_363_705:21*1024*1024):1_200_000;
    const padding=Buffer.alloc(Math.max(8,minimum-input.length));
    padding.writeUInt32BE(padding.length);padding.write('free',4);
    writeFileSync(file,Buffer.concat([input,padding]));
  }
});
test.afterAll(()=>{if(fixtures&&!process.env.ENCODER_STRESS_FIXTURE_DIR)rmSync(fixtures,{recursive:true,force:true});});
for(const [name] of samples) for(const decoderThreads of [null,1]) {
  test(`${name}, decoder threads ${decoderThreads??'default'}`,async({page,request},testInfo)=>{
    test.setTimeout(long?900000:120000);
    await page.addInitScript(threads=>{
      window.stressLogs=[];
      const NativeWorker=window.Worker;
      window.Worker=class extends NativeWorker {
        constructor(...args){
          super(...args);
          this.addEventListener('message',event=>{
            if(event.data?.type==='LOG'){
              window.stressLogs.push(event.data.data);
              if(window.stressLogs.length>200)window.stressLogs.shift();
            }
          });
        }
      };
      const send=Worker.prototype.postMessage;
      window.stressCommands=[];
      Worker.prototype.postMessage=function(message,...rest){
        if(message?.type==='EXEC'){
          window.stressCommands.push(message.data.args);
          // Experiment only: production encoder settings are unchanged.
          if(threads)message={...message,data:{...message.data,args:['-threads',String(threads),...message.data.args]}};
        }
        return send.call(this,message,...rest);
      };
    },decoderThreads);
    const session=await(await request.post('/test/session')).json();
    await page.goto(session.path);
    await page.locator('#clips').setInputFiles(join(fixtures,name+'.mp4'));
    const started=Date.now();
    await page.locator('#submit').click();
    await expect.poll(()=>page.locator('#phase-title').textContent(),{timeout:long?840000:90000}).toMatch(/Delivered to Discord|Action needed/);
    const result={sample:name,decoderThreads,seconds:(Date.now()-started)/1000,
      heading:await page.locator('#phase-title').textContent(),message:await page.locator('#message').textContent(),
      inputBytes:statSync(join(fixtures,name+'.mp4')).size,
      commands:await page.evaluate(()=>window.stressCommands),logs:await page.evaluate(()=>window.stressLogs)};
    if(long&&result.heading==='Delivered to Discord'&&process.env.BROWSER_TEST_CAPTURE_DIR){
      const token=session.path.split('/').pop();
      const output=join(process.env.BROWSER_TEST_CAPTURE_DIR,readdirSync(process.env.BROWSER_TEST_CAPTURE_DIR).find(file=>file===token+'-0.mp4'));
      result.output=JSON.parse(execFileSync('ffprobe',['-v','error','-show_format','-show_streams','-of','json',output],{encoding:'utf8'}));
      const video=result.output.streams.find(stream=>stream.codec_type==='video');
      const audio=result.output.streams.find(stream=>stream.codec_type==='audio');
      expect(video.codec_name).toBe('h264');expect(video.width).toBe(1280);expect(video.height).toBe(720);
      expect(audio.codec_name).toBe('aac');
      expect(Number(result.output.format.size)).toBeLessThanOrEqual(20*1024*1024);
      expect(Math.abs(Number(result.output.format.duration)-Number(name.endsWith('43s')?43:10))).toBeLessThan(0.25);
      execFileSync('ffmpeg',['-v','error','-xerror','-i',output,'-f','null','-'],{timeout:60000});
    }
    console.log(JSON.stringify({...result,commands:undefined,logs:result.logs.filter(log=>/error|memory|alloc|fail|thread/i.test(log.message))}));
    await testInfo.attach('synthetic-result',{body:JSON.stringify(result,null,2),contentType:'application/json'});
    expect(result.commands.length).toBeGreaterThan(0);
    // Current default 8K failures are observations, not regression passes.
    // The experimental one-thread path must actually complete and validate.
    if(!long||decoderThreads)expect(result.heading,result.message).toBe('Delivered to Discord');
  });
}
