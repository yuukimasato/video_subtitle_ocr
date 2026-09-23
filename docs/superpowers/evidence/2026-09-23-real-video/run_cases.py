import subprocess, os, json, time, hashlib
from pathlib import Path
root=Path(__file__).resolve().parent
repo=root.parents[1]/'video_subtitle_ocr'
cases=[('mail','[DMG] 冴えない彼女の育てかた♭ 第07話19.mp4','[DMG] 冴えない彼女の育てかた♭ 第07話19_roi.json','ch'),('phone11','11.mp4','11_roi.json','japan'),('phone12','12.mp4','12_roi.json','ch'),('opening4k','一周的朋友 NCOP(虹のかけら)_4k_high_quality_x264.mp4','一周的朋友 NCOP(虹のかけら)_4k_high_quality_x264_roi_autosave.json','ch')]
env=os.environ.copy();env.update(MODE='cpu',QT_QPA_PLATFORM='offscreen',OMP_NUM_THREADS='8',VLM_REFINE_BASE_URL='',VLM_REFINE_API_KEY='')
results=[]
for key,video,roi,lang in cases:
 cmd=['taskset','-c','0-7','/usr/bin/time','-v',str(repo/'.venv/bin/python'),str(repo/'cli.py'),str(repo.parent/'test'/video),'--roi-file',str(repo.parent/'test'/roi),'--engine','paddle','--model-tier','small','--lang',lang,'--workers','1','-o',str(root/'outputs'/f'{key}.ass')]
 t=time.time();print('START',key,flush=True)
 with (root/'logs'/f'{key}.log').open('w') as log:
  try:r=subprocess.run(cmd,cwd=repo,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=1200);code=r.returncode
  except subprocess.TimeoutExpired:code='timeout'
 result={'id':key,'command':cmd,'exit_code':code,'elapsed_seconds':time.time()-t,'source_sha':subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip(),'roi_sha256':hashlib.sha256((repo.parent/'test'/roi).read_bytes()).hexdigest(),'env_overrides':{k:env[k] for k in ['MODE','QT_QPA_PLATFORM','OMP_NUM_THREADS','VLM_REFINE_BASE_URL','VLM_REFINE_API_KEY']}}
 results.append(result);(root/'runs.json').write_text(json.dumps(results,ensure_ascii=False,indent=2));print('DONE',key,code,round(result['elapsed_seconds'],2),flush=True)
