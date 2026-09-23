from pathlib import Path
import json,subprocess
from PIL import Image,ImageDraw
r=Path(__file__).resolve().parent
video=next(x['video'] for x in json.loads((r/'inputs.json').read_text()) if x['id']=='mail')
for mode in ['raw','rendered','ass_only']:
 ins=['-i',video] if mode!='ass_only' else ['-f','lavfi','-i','color=c=0x303030:s=1920x1080:r=24000/1001']
 vf=('ass='+str(r/'outputs/mail_protected.ass')+',' if mode!='raw' else '')+"select='eq(n\\,222)'"
 subprocess.run(['ffmpeg','-v','error',*ins,'-vf',vf,'-frames:v','1','-y',str(r/'frames'/f'mail_protected_f222_{mode}.png')],check=True)
ims=[Image.open(r/'frames'/f'mail_protected_f222_{mode}.png').convert('RGB').crop((570,100,1380,1080)) for mode in ['raw','rendered','ass_only']]
for im in ims:im.thumbnail((640,900))
w=sum(im.width for im in ims);h=max(im.height for im in ims)
out=Image.new('RGB',(w,h+42),'#202020');d=ImageDraw.Draw(out);x=0
for label,im in zip(['SOURCE','SOURCE + ASS','ASS ONLY'],ims):
 d.text((x+5,8),f'{label} | mail protected f222 t=9.259',fill='white');out.paste(im,(x,42));x+=im.width
out.save(r/'frames/mail_protected_f222_comparison.jpg',quality=94)
