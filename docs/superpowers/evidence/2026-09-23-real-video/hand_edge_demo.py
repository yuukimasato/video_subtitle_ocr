from pathlib import Path
import cv2,numpy as np,json,subprocess
from PIL import Image,ImageDraw
r=Path(__file__).resolve().parent
video=next(x['video'] for x in json.loads((r/'inputs.json').read_text()) if x['id']=='mail')
base=(r/'outputs/mail_protected.ass').read_text(encoding='utf-8-sig')
reports=[]
for idx,f in enumerate([216,222,228],1):
 im=cv2.imread(str(r/'frames'/f'hand_demo_raw_{idx:02d}.png'))
 # Sample-specific feasibility probe. This is not a universal skin classifier.
 b,g,red=cv2.split(im.astype(np.int16));candidate=((red>140)&(g>100)&(b>70)&(red-g>7)&(g-b>4)&(red-g<65)&(g-b<65)).astype(np.uint8)
 domain=np.zeros(candidate.shape,np.uint8);domain[300:1080,630:1460]=1;candidate*=domain
 n,labels,stats,cent=cv2.connectedComponentsWithStats(candidate,8)
 keep=np.zeros(candidate.shape,np.uint8)
 for k in range(1,n):
  x,y,w,h,area=stats[k]
  if area>5000 and y+h>=1075:keep[labels==k]=1
 keep=cv2.morphologyEx(keep,cv2.MORPH_CLOSE,np.ones((5,5),np.uint8))
 # Strong fg/bg from reliable color components; let edges resolve 5px uncertainty band.
 mask=np.full(keep.shape,cv2.GC_BGD,np.uint8)
 dil=cv2.dilate(keep,np.ones((11,11),np.uint8));ero=cv2.erode(keep,np.ones((9,9),np.uint8))
 mask[dil>0]=cv2.GC_PR_BGD;mask[keep>0]=cv2.GC_PR_FGD;mask[ero>0]=cv2.GC_FGD
 cv2.grabCut(im,mask,None,np.zeros((1,65),np.float64),np.zeros((1,65),np.float64),3,cv2.GC_INIT_WITH_MASK)
 fg=np.isin(mask,[cv2.GC_FGD,cv2.GC_PR_FGD]).astype(np.uint8)
 contours,_=cv2.findContours(fg,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
 polys=[cv2.approxPolyDP(c,1.5,True).reshape(-1,2) for c in contours if cv2.contourArea(c)>5000]
 path=' '.join('m '+str(p[0,0])+' '+str(p[0,1])+' l '+' '.join(f'{x} {y}' for x,y in p[1:]) for p in polys)
 lines=[]
 for line in base.splitlines():
  if line.startswith('Dialogue:'):
   parts=line.split(',',9)
   if parts[1]=='0:00:06.54':parts[9]='{\\iclip('+path+')}'+parts[9]
   line=','.join(parts)
  lines.append(line)
 ass=r/'outputs'/f'mail_edge_demo_f{f}.ass';ass.write_text('\n'.join(lines),encoding='utf-8-sig')
 subprocess.run(['ffmpeg','-v','error','-i',video,'-vf',f"ass={ass},select='eq(n\\,{f})'",'-frames:v','1','-y',str(r/'frames'/f'hand_edge_rendered_f{f}.png')],check=True)
 overlay=im.copy();cv2.drawContours(overlay,polys,-1,(0,255,0),3)
 panels=[Image.fromarray(cv2.cvtColor(overlay,cv2.COLOR_BGR2RGB)),Image.open(r/'frames'/f'hand_edge_rendered_f{f}.png').convert('RGB')]
 out=Image.new('RGB',(1280,610),'#202020');d=ImageDraw.Draw(out)
 for j,(p,label) in enumerate(zip(panels,['SAMPLE-SPECIFIC COLOR + EDGE CONTOUR','ASS WITH FRAME-MATCHED ICLIP'])):
  p=p.crop((550,280,1480,1080));p.thumbnail((640,560));out.paste(p,(j*640,42));d.text((j*640+4,8),f'{label} | f{f}',fill='white')
 out.save(r/'frames'/f'hand_edge_f{f}.jpg',quality=94)
 reports.append({'frame':f,'polygons':[p.tolist() for p in polys],'vertices':[len(p) for p in polys],'area_px':int(fg.sum()),'scope':'sample-specific color seed + GrabCut; per-frame probe only, not production quality claim'})
(r/'hand_edge_demo.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2))
