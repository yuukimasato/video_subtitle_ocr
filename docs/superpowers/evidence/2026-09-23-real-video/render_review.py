import subprocess,json,re,sys
from pathlib import Path
from PIL import Image,ImageDraw
root=Path(__file__).resolve().parent
inputs={x['id']:x for x in json.loads((root/'inputs.json').read_text())}
def sec(s):
 h,m,s=s.split(':');return int(h)*3600+int(m)*60+float(s)
for key in sys.argv[1:]:
 inp=inputs[key]; ass=root/'outputs'/f'{key}.ass';fps=inp['fps']
 frames=sorted({round(t*fps) for t in inp['sample_seconds']})
 if key=='mail':frames=sorted(set(frames+[0,120,144,168,192,204,228,240]))
 events=[]
 for ln,line in enumerate(ass.read_text(encoding='utf-8-sig').splitlines(),1):
  if line.startswith(('Dialogue:','Comment:')):
   parts=line.split(':',1)[1].strip().split(',',9)
   events.append({'line':ln,'kind':line.split(':',1)[0],'start':sec(parts[1]),'end':sec(parts[2]),'style':parts[3],'name':parts[4],'text':re.sub(r'\{[^}]*\}','',parts[9]),'raw':parts[9]})
 (root/'outputs'/f'{key}_events.json').write_text(json.dumps(events,ensure_ascii=False,indent=2))
 select='+'.join(f'eq(n\\,{f})' for f in frames)
 for mode in ['raw','rendered','ass_only']:
  if mode=='ass_only':
   probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0','-show_entries','stream=width,height','-of','json',inp['video']]))['streams'][0]
   ins=['-f','lavfi','-i',f"color=c=0x303030:s={probe['width']}x{probe['height']}:r={fps}"]
  else:ins=['-i',inp['video']]
  filters=(f'ass={ass},' if mode!='raw' else '')+f"select='{select}'"
  cmd=['ffmpeg','-v','error','-threads','2',*ins,'-vf',filters,'-frames:v',str(len(frames)),'-fps_mode','vfr','-y',str(root/'frames'/f'{key}_{mode}_%02d.png')]
  subprocess.run(cmd,check=True)
 sample=[]
 for i,f in enumerate(frames,1):
  active=[e for e in events if e['start']<=f/fps<e['end']]
  sample.append({'frame':f,'t':f/fps,'active':active})
  imgs=[Image.open(root/'frames'/f'{key}_{mode}_{i:02d}.png').convert('RGB') for mode in ['raw','rendered','ass_only']]
  # Crop to the scene panel for phone/mail; preserve full-screen for 4K bands.
  crop={'phone11':(300,90,950,1040),'phone12':(40,90,700,1040),'mail':(570,100,1380,1080)}.get(key)
  if crop:imgs=[im.crop(crop) for im in imgs]
  panels=[]
  for im in imgs:
   im.thumbnail((640,900));panels.append(im)
  h=max(im.height for im in panels);w=sum(im.width for im in panels)
  out=Image.new('RGB',(w,h+42),'#202020');d=ImageDraw.Draw(out);x=0
  for label,im in zip(['SOURCE','SOURCE + ASS','ASS ONLY'],panels):
   d.text((x+5,8),f'{label} | {key} f{f} t={f/fps:.3f}',fill='white');out.paste(im,(x,42));x+=im.width
  out.save(root/'frames'/f'{key}_f{f}_comparison.jpg',quality=94)
 (root/'outputs'/f'{key}_samples.json').write_text(json.dumps(sample,ensure_ascii=False,indent=2))
 print(key,'events',len(events),'frames',len(frames),flush=True)
