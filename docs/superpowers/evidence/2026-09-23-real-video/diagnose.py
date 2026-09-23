import os,sys,json
from pathlib import Path
root=Path(__file__).resolve().parent;repo=root.parents[1]/'video_subtitle_ocr';sys.path.insert(0,str(repo))
os.environ.update(MODE='cpu',QT_QPA_PLATFORM='offscreen',OMP_NUM_THREADS='8',VLM_REFINE_BASE_URL='',VLM_REFINE_API_KEY='')
from PySide6.QtCore import QCoreApplication
app=QCoreApplication([])
from core.subtitle_generator import OCRToASSOptimizer
opt=OCRToASSOptimizer(video_path='unused',output_path='unused',fps=24000/1001,width=1920,height=1080)
events=[{'roi':'roi_0','style':'Scene','start_time':'0:00:01.00','end_time':'0:00:01.12','tags':'{\\pos(100,100)}','body':'first'}, {'roi':'roi_0','style':'Scene','start_time':'0:00:01.00','end_time':'0:00:01.12','tags':'{\\pos(100,200)}','body':'second'}]
result=opt._clamp_same_roi_event_overlaps(events)
(root/'zero_duration_repro.json').write_text(json.dumps(result,indent=2))
from core import occlusion_mask as occ
from core.pipeline_worker import collect_motion_roi_specs
from scripts.motion_ass import build_motion_events
conf=json.loads((repo.parent/'test/[DMG] 冴えない彼女の育てかた♭ 第07話19_roi.json').read_text()); spec=collect_motion_roi_specs(conf['rois'])[0]
original=occ.attach_occlusion_clips
report={}
def attach(events,*,tracks,line_tracks,occlusions,cfg):
 report['detected_frames']=sorted(occlusions);report['event_count']=len(events);report['line_indices']=[e.get('line_idx') for e in events];report['line_boxes']=[list(lt.ref_box) for lt in line_tracks];report['occlusion_boxes']={str(f):[[float(p[:,0].min()),float(p[:,1].min()),float(p[:,0].max()),float(p[:,1].max())] for p in polys] for f,polys in occlusions.items()}
 tmap={t.frame_num:t for t in tracks}; details=[]
 def sec(s):
  h,m,t=s.split(':');return int(h)*3600+int(m)*60+float(t)
 for ev in events:
  li=ev.get('line_idx');st,en=sec(ev['start_time']),sec(ev['end_time'])
  span=sorted(f for f,t in tmap.items() if t.status=='ok' and t.time_sec is not None and st-1e-6<=t.time_sec<=en+1e-6)
  chosen=occ._even_subset(span,cfg.sample_max_frames)
  details.append({'line':li,'interval':[st,en],'detected_in_span':sorted(set(span)&set(occlusions)),'chosen_hits':sorted(set(chosen)&set(occlusions)), 'max_overlap':max((occ._poly_box_overlap_ratio(p,line_tracks[li].ref_box) for f in chosen for p in occlusions.get(f,[])),default=0) if isinstance(li,int) else None})
 report['details']=details
 out=original(events,tracks=tracks,line_tracks=line_tracks,occlusions=occlusions,cfg=cfg);report['clipped']=sum('\\iclip' in e['tags'] for e in out)
 (root/'occlusion_diagnostics.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));return out
occ.attach_occlusion_clips=attach
build_motion_events(str(repo.parent/'test/[DMG] 冴えない彼女の育てかた♭ 第07話19.mp4'),spec['quad'],start_frame=0,end_frame=245,ocr_engine='paddle',occlusion_clip=True,auto_brightness=True,scene_text_policy='overlap',log=lambda s:print(s,flush=True))
