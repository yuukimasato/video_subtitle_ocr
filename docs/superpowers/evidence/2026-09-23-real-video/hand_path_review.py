import json,subprocess,re
from pathlib import Path
import cv2,numpy as np
from PIL import Image,ImageDraw
r=Path(__file__).resolve().parent
video=next(x['video'] for x in json.loads((r/'inputs.json').read_text()) if x['id']=='mail')
path=(r/'user_hand_path.txt').read_text().strip()
nums=list(map(int,re.findall(r'\d+',path)));poly=np.array(nums).reshape(-1,2)
s=(r/'outputs/mail_protected.ass').read_text(encoding='utf-8-sig')
lines=[]
for line in s.splitlines():
 if line.startswith('Dialogue:'):
  p=line.split(',',9)
  if p[1]=='0:00:06.54':p[9]='{\\iclip('+path+')}'+p[9]
  line=','.join(p)
 lines.append(line)
ass=r/'outputs/mail_user_clip_demo.ass';ass.write_text('\n'.join(lines),encoding='utf-8-sig')
select="select='eq(n\\,216)+eq(n\\,222)+eq(n\\,228)'"
for mode in ['raw','userclip']:
 vf=('ass='+str(ass)+',' if mode=='userclip' else '')+select
 subprocess.run(['ffmpeg','-v','error','-i',video,'-vf',vf,'-frames:v','3','-fps_mode','vfr','-y',str(r/'frames'/f'hand_demo_{mode}_%02d.png')],check=True)
metrics=[]
for idx,f in enumerate([216,222,228],1):
 im=cv2.imread(str(r/'frames'/f'hand_demo_raw_{idx:02d}.png'))
 overlay=im.copy();cv2.polylines(overlay,[poly.astype(np.int32)],True,(0,0,255),3)
 # Feasibility prototype only: bounded screen window, seeds from provided contour + appearance.
 mask=np.zeros(im.shape[:2],np.uint8)
 mask[280:1080,600:1460]=cv2.GC_PR_BGD
 fill=np.zeros(im.shape[:2],np.uint8);cv2.fillPoly(fill,[poly.astype(np.int32)],1)
 mask[fill>0]=cv2.GC_PR_FGD
 # Eroded contour gives sure hand seeds, outside dilated polygon sure background.
 eroded=cv2.erode(fill,np.ones((17,17),np.uint8));mask[eroded>0]=cv2.GC_FGD
 cv2.grabCut(im,mask,None,np.zeros((1,65),np.float64),np.zeros((1,65),np.float64),3,cv2.GC_INIT_WITH_MASK)
 fg=np.isin(mask,[cv2.GC_FGD,cv2.GC_PR_FGD]).astype(np.uint8)
 contours,_=cv2.findContours(fg,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
 contours=[cv2.approxPolyDP(c,2.0,True) for c in contours if cv2.contourArea(c)>400]
 auto=im.copy();cv2.drawContours(auto,contours,-1,(0,255,0),3)
 # Diagnostic polygon artifact in screen coordinates, not automatic production tracking.
 metrics.append({'frame':f,'seed':'user polygon, eroded 17px','method':'GrabCut 3 iterations, seeded demo','vertices':[len(c) for c in contours],'screen_polygons':[c.reshape(-1,2).tolist() for c in contours]})
 panels=[Image.fromarray(cv2.cvtColor(x,cv2.COLOR_BGR2RGB)) for x in [overlay,auto]]+[Image.open(r/'frames'/f'hand_demo_userclip_{idx:02d}.png').convert('RGB')]
 out=Image.new('RGB',(1920,760),'#202020');d=ImageDraw.Draw(out)
 for j,(p,label) in enumerate(zip(panels,['USER OUTLINE (RED)','SEEDED EDGE REFINEMENT (GREEN)','ASS WITH USER ICLIP'])):
  p=p.crop((550,280,1480,1080));p.thumbnail((640,710));out.paste(p,(j*640,42));d.text((j*640+4,8),f'{label} | f{f}',fill='white')
 out.save(r/'frames'/f'hand_path_f{f}.jpg',quality=94)
(r/'hand_contour_demo.json').write_text(json.dumps(metrics,ensure_ascii=False,indent=2))
