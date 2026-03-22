#!/usr/bin/env python3
"""
GeoScout v2.0 - Street-Level Image Search
Upload a photo, draw a search area, find where it was taken.

Providers:
  - Mapillary (free, crowdsourced, global)
  - Google Street View (paid, best quality, no China)
  - Baidu Panorama (free-ish, best China coverage)
  - Satellite tiles (ESRI/Sentinel/Google overhead imagery)
"""

import os, sys, io, json, math, time, hashlib, threading, uuid, csv
from datetime import datetime
from flask import Flask, request, jsonify, render_template, send_from_directory, send_file
from PIL import Image
import numpy as np
import urllib.request

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__,
            template_folder=os.path.join(BASE_DIR, 'templates'),
            static_folder=os.path.join(BASE_DIR, 'static'))
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024

UPLOAD_DIR = os.path.join(BASE_DIR, 'uploads')
RESULT_DIR = os.path.join(BASE_DIR, 'results')
STATIC_DIR = os.path.join(BASE_DIR, 'static')
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(RESULT_DIR, exist_ok=True)

scans = {}

# ── Coordinate conversion (WGS84 → BD09 for Baidu) ──────────

X_PI = math.pi * 3000.0 / 180.0

def _tlat(x, y):
    r = -100+2*x+3*y+.2*y*y+.1*x*y+.2*math.sqrt(abs(x))
    r += (20*math.sin(6*x*math.pi)+20*math.sin(2*x*math.pi))*2/3
    r += (20*math.sin(y*math.pi)+40*math.sin(y/3*math.pi))*2/3
    r += (160*math.sin(y/12*math.pi)+320*math.sin(y*math.pi/30))*2/3
    return r

def _tlng(x, y):
    r = 300+x+2*y+.1*x*x+.1*x*y+.1*math.sqrt(abs(x))
    r += (20*math.sin(6*x*math.pi)+20*math.sin(2*x*math.pi))*2/3
    r += (20*math.sin(x*math.pi)+40*math.sin(x/3*math.pi))*2/3
    r += (150*math.sin(x/12*math.pi)+300*math.sin(x/30*math.pi))*2/3
    return r

def wgs84_to_bd09(lat, lng):
    a, ee = 6378245.0, 0.00669342162296594323
    dl = _tlat(lng-105, lat-35); dn = _tlng(lng-105, lat-35)
    rl = lat/180*math.pi; m = math.sin(rl); m = 1-ee*m*m; sm = math.sqrt(m)
    dl = (dl*180)/((a*(1-ee))/(m*sm)*math.pi)
    dn = (dn*180)/(a/sm*math.cos(rl)*math.pi)
    gl, gn = lat+dl, lng+dn
    z = math.sqrt(gn*gn+gl*gl)+2e-5*math.sin(gl*X_PI)
    t = math.atan2(gl, gn)+3e-6*math.cos(gn*X_PI)
    return z*math.sin(t)+.006, z*math.cos(t)+.0065

# ── Image comparison engine ──────────────────────────────────

def phash_fast(img, size=8):
    img = img.convert('L').resize((size+1, size), Image.LANCZOS)
    px = np.array(img)
    return ''.join('1' if px[y,x]>px[y,x+1] else '0' for y in range(size) for x in range(size))

def phash_similarity(a, b):
    h1, h2 = phash_fast(a), phash_fast(b)
    return max(0, 1-sum(c!=d for c,d in zip(h1,h2))/len(h1))

def ssim_similarity(a, b, sz=64):
    i1 = np.array(a.convert('L').resize((sz,sz),Image.LANCZOS),dtype=np.float64)
    i2 = np.array(b.convert('L').resize((sz,sz),Image.LANCZOS),dtype=np.float64)
    m1,m2=i1.mean(),i2.mean(); s1,s2=i1.var(),i2.var()
    s12=((i1-m1)*(i2-m2)).mean(); c1=(0.01*255)**2; c2=(0.03*255)**2
    n=(2*m1*m2+c1)*(2*s12+c2); d=(m1**2+m2**2+c1)*(s1+s2+c2)
    return max(0,min(1,((n/d if d else 0)+1)/2))

def hist_similarity(a, b, bins=64):
    sc=[]
    for ch in range(3):
        h1=np.histogram(np.array(a.convert('RGB'))[:,:,ch],bins=bins,range=(0,256))[0].astype(float)
        h2=np.histogram(np.array(b.convert('RGB'))[:,:,ch],bins=bins,range=(0,256))[0].astype(float)
        h1/=(h1.sum()+1e-10); h2/=(h2.sum()+1e-10); sc.append(np.minimum(h1,h2).sum())
    return sum(sc)/len(sc)

def tmpl_similarity(a, b, sz=128):
    tgt=np.array(b.convert('L').resize((sz,sz),Image.LANCZOS),dtype=np.float64)
    best=0
    for sc in [0.5,0.75,1.0]:
        s=max(16,int(sz*sc))
        if s>sz: continue
        tpl=np.array(a.convert('L').resize((s,s),Image.LANCZOS),dtype=np.float64)
        tm,ts=tpl.mean(),tpl.std()+1e-10
        for y in range(0,sz-s+1,max(1,s//4)):
            for x in range(0,sz-s+1,max(1,s//4)):
                r=tgt[y:y+s,x:x+s]; rm,rs=r.mean(),r.std()+1e-10
                ncc=((tpl-tm)*(r-rm)).mean()/(ts*rs)
                best=max(best,(ncc+1)/2)
    return best

def compare_images(ref, cand, weights):
    funcs = {'phash':phash_similarity,'ssim':ssim_similarity,'histogram':hist_similarity,'template':tmpl_similarity}
    scores={}; tw=0
    for name,fn in funcs.items():
        w=weights.get(name,0)
        if w>0: scores[name]=fn(ref,cand); tw+=w
    if tw==0: return 0, scores
    return sum(scores.get(m,0)*weights.get(m,0) for m in scores)/tw, scores

# ── Shared utils ─────────────────────────────────────────────

def fetch_image(url, timeout=15):
    try:
        req = urllib.request.Request(url, headers={'User-Agent':'GeoScout/2.0'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            if len(data)<1000: return None
            return Image.open(io.BytesIO(data)).convert('RGB')
    except: return None

def grid_points(bounds, spacing_m):
    s,w,n,e = bounds['south'],bounds['west'],bounds['north'],bounds['east']
    dlat = spacing_m/111320.0; dlng = spacing_m/(111320.0*math.cos(math.radians((s+n)/2)))
    pts=[]; lat=s
    while lat<=n:
        lng=w
        while lng<=e: pts.append((lat,lng)); lng+=dlng
        lat+=dlat
    return pts

def fine_points(clat, clng, radius_m, spacing_m):
    dlat=radius_m/111320.0; dlng=radius_m/(111320.0*math.cos(math.radians(clat)))
    slat=spacing_m/111320.0; slng=spacing_m/(111320.0*math.cos(math.radians(clat)))
    pts=[]; lat=clat-dlat
    while lat<=clat+dlat:
        lng=clng-dlng
        while lng<=clng+dlng: pts.append((lat,lng)); lng+=slng
        lat+=slat
    return pts

def new_scan(sid, mode):
    return {'id':sid,'mode':mode,'phase':'starting','scanned':0,'total_points':0,
            'fine_scanned':0,'total_fine_points':0,'images_fetched':0,'images_compared':0,
            'no_coverage':0,'matches':0,'results':[],'completed':False,'cancelled':False,
            'error':None,'started':datetime.now().isoformat()}

# ── MAPILLARY (free) ─────────────────────────────────────────

def mly_query(bounds, token, limit=2000):
    bbox=f"{bounds['west']},{bounds['south']},{bounds['east']},{bounds['north']}"
    url=(f"https://graph.mapillary.com/images?access_token={token}"
         f"&fields=id,thumb_1024_url,thumb_256_url,computed_geometry,compass_angle,captured_at"
         f"&bbox={bbox}&limit={min(limit,2000)}")
    try:
        req=urllib.request.Request(url, headers={'User-Agent':'GeoScout/2.0'})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read()).get('data',[])
    except: return []

def mly_search(bounds, token):
    area = (bounds['east']-bounds['west'])*(bounds['north']-bounds['south'])
    if area <= 0.008:
        return mly_query(bounds, token)
    # Split into sub-boxes
    all_imgs=[]; step=0.08
    lat=bounds['south']
    while lat<bounds['north']:
        lng=bounds['west']
        while lng<bounds['east']:
            sub={'south':lat,'west':lng,'north':min(lat+step,bounds['north']),'east':min(lng+step,bounds['east'])}
            all_imgs.extend(mly_query(sub, token, 500)); lng+=step
        lat+=step
    seen=set(); return [i for i in all_imgs if i['id'] not in seen and not seen.add(i['id'])]

def run_mapillary(sid, ref, bounds, params):
    scan=scans[sid]; token=params['api_key']; thresh=params['threshold']
    weights=params['weights']; rl=params.get('rate_limit',100)/1000; mx=params.get('max_results',200)
    try:
        scan['phase']='searching'
        imgs=mly_search(bounds, token); scan['total_points']=len(imgs)
        if not imgs:
            scan['phase']='done'; scan['completed']=True
            scan['error']='No Mapillary images found in this area.'; return
        scan['phase']='comparing'
        for i,im in enumerate(imgs):
            if scan.get('cancelled') or len(scan['results'])>=mx: break
            scan['scanned']=i+1
            turl=im.get('thumb_1024_url') or im.get('thumb_256_url')
            if not turl: continue
            cand=fetch_image(turl); scan['images_fetched']+=1
            if not cand: continue
            scan['images_compared']+=1
            score,ms=compare_images(ref,cand,weights)
            if score>=thresh:
                coords=im.get('computed_geometry',{}).get('coordinates',[0,0])
                scan['results'].append({
                    'lat':coords[1],'lng':coords[0],'score':round(score*100,1),
                    'heading':round(im.get('compass_angle',0),1),
                    'scores':{k:round(v*100,1) for k,v in ms.items()}, 'phase':'mapillary',
                    'image_id':im.get('id'),
                    'sv_url':f"https://www.mapillary.com/app/?focus=photo&pKey={im.get('id')}",
                    'gmaps':f"https://www.google.com/maps/@{coords[1]},{coords[0]},18z"
                })
                scan['matches']=len(scan['results'])
            if rl>0: time.sleep(rl)
        scan['phase']='done'; scan['completed']=True
    except Exception as e: scan['error']=str(e); scan['phase']='error'

# ── GOOGLE STREET VIEW (paid) ────────────────────────────────

def run_google(sid, ref, bounds, params):
    scan=scans[sid]; key=params['api_key']; thresh=params['threshold']
    weights=params['weights']; cs=params.get('coarse_spacing',200); fs=params.get('fine_spacing',30)
    fr=params.get('fine_radius',100); ch=params.get('coarse_headings',[0,90,180,270])
    fh=params.get('fine_headings',[0,45,90,135,180,225,270,315])
    rl=params.get('rate_limit',50)/1000; mx=params.get('max_results',200)
    def meta(lat,lng):
        try:
            req=urllib.request.Request(f"https://maps.googleapis.com/maps/api/streetview/metadata?location={lat},{lng}&key={key}",headers={'User-Agent':'GeoScout/2.0'})
            with urllib.request.urlopen(req,timeout=10) as r:
                d=json.loads(r.read())
                if d.get('status')=='OK':
                    l=d.get('location',{}); return True,l.get('lat',lat),l.get('lng',lng)
        except: pass
        return False,lat,lng
    def svurl(la,ln,h): return f"https://maps.googleapis.com/maps/api/streetview?size=640x640&location={la},{ln}&heading={h}&fov=90&pitch=0&key={key}"
    try:
        scan['phase']='coarse'; pts=grid_points(bounds,cs); scan['total_points']=len(pts)
        hits=[]; checked=set()
        for i,(lat,lng) in enumerate(pts):
            if scan.get('cancelled'): return
            scan['scanned']=i+1; ok,al,an=meta(lat,lng)
            if not ok: scan['no_coverage']+=1; continue
            lk=(round(al,5),round(an,5))
            if lk in checked: continue
            checked.add(lk)
            bs,bh,bm=0,0,{}
            for h in ch:
                img=fetch_image(svurl(al,an,h)); scan['images_fetched']+=1
                if img:
                    s,m=compare_images(ref,img,weights)
                    if s>bs: bs,bh,bm=s,h,m
                if rl>0: time.sleep(rl)
            ct=max(0.3,thresh-0.15)
            if bs>=thresh:
                scan['results'].append({'lat':al,'lng':an,'score':round(bs*100,1),'heading':bh,
                    'scores':{k:round(v*100,1) for k,v in bm.items()},'phase':'coarse',
                    'sv_url':f"https://www.google.com/maps/@{al},{an},3a,75y,{bh}h,90t"})
                scan['matches']=len(scan['results']); hits.append((al,an))
            elif bs>=ct: hits.append((al,an))
        if hits and not scan.get('cancelled'):
            scan['phase']='fine'; fa=[]
            for hl,hn in hits: fa.extend(fine_points(hl,hn,fr,fs))
            seen=set(); uf=[(a,b) for a,b in fa if (k:=(round(a,5),round(b,5))) not in checked and k not in seen and not seen.add(k)]
            scan['total_fine_points']=len(uf)
            for i,(lat,lng) in enumerate(uf):
                if scan.get('cancelled') or len(scan['results'])>=mx: break
                scan['fine_scanned']=i+1; ok,al,an=meta(lat,lng)
                if not ok: continue
                lk=(round(al,5),round(an,5))
                if lk in checked: continue
                checked.add(lk); bs,bh,bm=0,0,{}
                for h in fh:
                    img=fetch_image(svurl(al,an,h)); scan['images_fetched']+=1
                    if img:
                        s,m=compare_images(ref,img,weights)
                        if s>bs: bs,bh,bm=s,h,m
                    if rl>0: time.sleep(rl)
                if bs>=thresh:
                    scan['results'].append({'lat':al,'lng':an,'score':round(bs*100,1),'heading':bh,
                        'scores':{k:round(v*100,1) for k,v in bm.items()},'phase':'fine',
                        'sv_url':f"https://www.google.com/maps/@{al},{an},3a,75y,{bh}h,90t"})
                    scan['matches']=len(scan['results'])
        scan['phase']='done'; scan['completed']=True
    except Exception as e: scan['error']=str(e); scan['phase']='error'

# ── BAIDU PANORAMA (China) ───────────────────────────────────

def run_baidu(sid, ref, bounds, params):
    scan=scans[sid]; key=params['api_key']; thresh=params['threshold']
    weights=params['weights']; cs=params.get('coarse_spacing',200); fs=params.get('fine_spacing',30)
    fr=params.get('fine_radius',100); rl=params.get('rate_limit',100)/1000; mx=params.get('max_results',200)
    def burl(bl,bn,h=0,w=640,ht=320):
        return f"https://api.map.baidu.com/panorama/v2?ak={key}&width={w}&height={ht}&location={bn},{bl}&heading={h}&fov=90"
    def has_cov(bl,bn):
        try:
            req=urllib.request.Request(f"https://api.map.baidu.com/panorama/v2?ak={key}&width=64&height=32&location={bn},{bl}&fov=90",headers={'User-Agent':'GeoScout/2.0'})
            with urllib.request.urlopen(req,timeout=10) as r: return len(r.read())>2000
        except: return False
    hc=[0,90,180,270]; hf=[0,45,90,135,180,225,270,315]
    try:
        scan['phase']='coarse'; pts=grid_points(bounds,cs); scan['total_points']=len(pts)
        hits=[]; checked=set()
        for i,(lat,lng) in enumerate(pts):
            if scan.get('cancelled'): return
            scan['scanned']=i+1; bl,bn=wgs84_to_bd09(lat,lng)
            lk=(round(lat,4),round(lng,4))
            if lk in checked: continue
            checked.add(lk)
            if not has_cov(bl,bn): scan['no_coverage']+=1; continue
            bs,bh,bm=0,0,{}
            for h in hc:
                img=fetch_image(burl(bl,bn,h)); scan['images_fetched']+=1
                if img:
                    s,m=compare_images(ref,img,weights)
                    if s>bs: bs,bh,bm=s,h,m
                if rl>0: time.sleep(rl)
            ct=max(0.3,thresh-0.15)
            if bs>=thresh:
                scan['results'].append({'lat':lat,'lng':lng,'score':round(bs*100,1),'heading':bh,
                    'scores':{k:round(v*100,1) for k,v in bm.items()},'phase':'coarse',
                    'sv_url':f"https://map.baidu.com/@{bn*100000},{bl*100000},21z,87t,-{bh}h",
                    'gmaps':f"https://www.google.com/maps/@{lat},{lng},18z"})
                scan['matches']=len(scan['results']); hits.append((lat,lng))
            elif bs>=ct: hits.append((lat,lng))
        if hits and not scan.get('cancelled'):
            scan['phase']='fine'; fa=[]
            for hl,hn in hits: fa.extend(fine_points(hl,hn,fr,fs))
            seen=set(); uf=[(a,b) for a,b in fa if (k:=(round(a,4),round(b,4))) not in checked and k not in seen and not seen.add(k)]
            scan['total_fine_points']=len(uf)
            for i,(lat,lng) in enumerate(uf):
                if scan.get('cancelled') or len(scan['results'])>=mx: break
                scan['fine_scanned']=i+1; bl,bn=wgs84_to_bd09(lat,lng)
                if not has_cov(bl,bn): continue
                lk=(round(lat,4),round(lng,4))
                if lk in checked: continue
                checked.add(lk); bs,bh,bm=0,0,{}
                for h in hf:
                    img=fetch_image(burl(bl,bn,h)); scan['images_fetched']+=1
                    if img:
                        s,m=compare_images(ref,img,weights)
                        if s>bs: bs,bh,bm=s,h,m
                    if rl>0: time.sleep(rl)
                if bs>=thresh:
                    scan['results'].append({'lat':lat,'lng':lng,'score':round(bs*100,1),'heading':bh,
                        'scores':{k:round(v*100,1) for k,v in bm.items()},'phase':'fine',
                        'sv_url':f"https://map.baidu.com/@{bn*100000},{bl*100000},21z,87t,-{bh}h",
                        'gmaps':f"https://www.google.com/maps/@{lat},{lng},18z"})
                    scan['matches']=len(scan['results'])
        scan['phase']='done'; scan['completed']=True
    except Exception as e: scan['error']=str(e); scan['phase']='error'

# ── SATELLITE ────────────────────────────────────────────────

def ll2t(lat,lng,z):
    n=2**z; x=int((lng+180)/360*n); lr=math.radians(lat)
    y=int((1-math.log(math.tan(lr)+1/math.cos(lr))/math.pi)/2*n); return x,y
def t2ll(x,y,z):
    n=2**z; return math.degrees(math.atan(math.sinh(math.pi*(1-2*y/n)))), x/n*360-180
def turl(src,x,y,z):
    return {'esri':f"https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
            'sentinel':f"https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2021_3857/default/GoogleMapsCompatible/{z}/{y}/{x}.jpg",
            'google':f"https://khms1.google.com/kh/v=984&x={x}&y={y}&z={z}"}.get(src,f"https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}")

def run_satellite(sid, ref, bounds, params):
    scan=scans[sid]; src=params.get('source','esri'); thresh=params['threshold']
    weights=params['weights']; cz=params.get('coarse_zoom',14); fz=params.get('fine_zoom',18)
    rl=params.get('rate_limit',50)/1000; mx=params.get('max_results',200)
    try:
        scan['phase']='coarse'
        x1,y1=ll2t(bounds['north'],bounds['west'],cz); x2,y2=ll2t(bounds['south'],bounds['east'],cz)
        if x1>x2: x1,x2=x2,x1
        if y1>y2: y1,y2=y2,y1
        ct=[(x,y) for x in range(x1,x2+1) for y in range(y1,y2+1)]
        scan['total_points']=len(ct); ht=[]
        for i,(x,y) in enumerate(ct):
            if scan.get('cancelled'): return
            scan['scanned']=i+1; img=fetch_image(turl(src,x,y,cz)); scan['images_fetched']+=1
            if img:
                s,ms=compare_images(ref,img,weights)
                if s>=thresh:
                    la,ln=t2ll(x+.5,y+.5,cz)
                    scan['results'].append({'lat':la,'lng':ln,'score':round(s*100,1),
                        'scores':{k:round(v*100,1) for k,v in ms.items()},'phase':'coarse',
                        'gmaps':f"https://www.google.com/maps/@{la},{ln},{fz}z"})
                    scan['matches']=len(scan['results'])
                if s>=max(0.3,thresh-.15): ht.append((x,y))
            if rl>0: time.sleep(rl)
        if ht and not scan.get('cancelled'):
            scan['phase']='fine'; r=2**(fz-cz)
            fs=set()
            for cx,cy in ht:
                for fx in range(cx*r,(cx+1)*r):
                    for fy in range(cy*r,(cy+1)*r): fs.add((fx,fy))
            fl=list(fs); scan['total_fine_points']=len(fl)
            for i,(x,y) in enumerate(fl):
                if scan.get('cancelled') or len(scan['results'])>=mx: break
                scan['fine_scanned']=i+1; img=fetch_image(turl(src,x,y,fz)); scan['images_fetched']+=1
                if img:
                    s,ms=compare_images(ref,img,weights)
                    if s>=thresh:
                        la,ln=t2ll(x+.5,y+.5,fz)
                        scan['results'].append({'lat':la,'lng':ln,'score':round(s*100,1),
                            'scores':{k:round(v*100,1) for k,v in ms.items()},'phase':'fine',
                            'gmaps':f"https://www.google.com/maps/@{la},{ln},{fz}z"})
                        scan['matches']=len(scan['results'])
                if rl>0: time.sleep(rl)
        scan['phase']='done'; scan['completed']=True
    except Exception as e: scan['error']=str(e); scan['phase']='error'

# ── Estimates ────────────────────────────────────────────────

def estimate(data):
    b=data['bounds']; mode=data.get('mode','mapillary')
    if mode=='mapillary':
        area=(b['east']-b['west'])*(b['north']-b['south'])
        subs=max(1,int(area/0.008)+1) if area>0.008 else 1
        return {'sub_boxes':subs,'est_images':subs*500,'cost':0,'note':'Free. Count depends on coverage.'}
    elif mode=='google':
        pts=grid_points(b,data.get('coarse_spacing',200)); n=len(pts)
        nc=len(data.get('coarse_headings',[0,90,180,270]))
        nf=len(data.get('fine_headings',[0,45,90,135,180,225,270,315]))
        ci=int(n*0.6*nc); fi=int(n*0.6*0.1*len(fine_points(0,0,data.get('fine_radius',100),data.get('fine_spacing',30)))*nf)
        t=ci+fi
        return {'coarse_points':n,'coarse_images':ci,'fine_images':fi,'total_images':t,'cost':round(t*0.007,2),'free_pct':round(min(100,28571/max(1,t)*100),1)}
    elif mode=='baidu':
        pts=grid_points(b,data.get('coarse_spacing',200)); n=len(pts)
        ci=int(n*0.5*4); fi=int(n*0.5*0.1*len(fine_points(0,0,100,30))*8)
        return {'coarse_points':n,'coarse_images':ci,'fine_images':fi,'total_images':ci+fi,'cost':0,'note':'Free (Baidu API).'}
    elif mode=='satellite':
        cz=data.get('coarse_zoom',14); fz=data.get('fine_zoom',18)
        x1,y1=ll2t(b['north'],b['west'],cz); x2,y2=ll2t(b['south'],b['east'],cz)
        c=max(1,(abs(x2-x1)+1)*(abs(y2-y1)+1)); f=int(c*(2**(fz-cz))**2*0.1)
        return {'coarse_tiles':c,'fine_tiles':f,'total_tiles':c+f,'cost':0}
    return {}

# ── Routes ───────────────────────────────────────────────────

@app.route('/')
def index(): return render_template('index.html')

@app.route('/static/<path:path>')
def serve_static(path): return send_from_directory(STATIC_DIR, path)

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'image' not in request.files: return jsonify({'error':'No image'}),400
    f=request.files['image']
    if not f.filename: return jsonify({'error':'No file'}),400
    ext=os.path.splitext(f.filename)[1].lower()
    if ext not in ('.jpg','.jpeg','.png','.webp','.bmp'): return jsonify({'error':'Bad format'}),400
    uid=hashlib.md5(f.read()).hexdigest()[:8]; f.seek(0)
    fn=f"{uid}{ext}"; f.save(os.path.join(UPLOAD_DIR,fn))
    img=Image.open(os.path.join(UPLOAD_DIR,fn))
    return jsonify({'filename':fn,'width':img.size[0],'height':img.size[1]})

@app.route('/estimate', methods=['POST'])
def est(): return jsonify(estimate(request.json))

@app.route('/scan', methods=['POST'])
def start():
    d=request.json; fn=d.get('filename')
    if not fn: return jsonify({'error':'No image'}),400
    fp=os.path.join(UPLOAD_DIR,fn)
    if not os.path.exists(fp): return jsonify({'error':'Not found'}),404
    ref=Image.open(fp).convert('RGB'); mode=d.get('mode','mapillary')
    sid=str(uuid.uuid4())[:8]; scans[sid]=new_scan(sid,mode)
    p={'threshold':d.get('threshold',55)/100,'api_key':d.get('api_key',''),
       'weights':{'phash':d.get('w_phash',30),'ssim':d.get('w_ssim',30),'histogram':d.get('w_histogram',20),'template':d.get('w_template',20)},
       'rate_limit':d.get('rate_limit',50),'max_results':d.get('max_results',200)}
    if mode in ('google','baidu'):
        p.update({'coarse_spacing':d.get('coarse_spacing',200),'fine_spacing':d.get('fine_spacing',30),
                  'fine_radius':d.get('fine_radius',100),'coarse_headings':d.get('coarse_headings',[0,90,180,270]),
                  'fine_headings':d.get('fine_headings',[0,45,90,135,180,225,270,315])})
    elif mode=='satellite':
        p.update({'source':d.get('source','esri'),'coarse_zoom':d.get('coarse_zoom',14),'fine_zoom':d.get('fine_zoom',18)})
    engines={'mapillary':run_mapillary,'google':run_google,'baidu':run_baidu,'satellite':run_satellite}
    t=threading.Thread(target=engines.get(mode,run_mapillary),args=(sid,ref,d['bounds'],p)); t.daemon=True; t.start()
    return jsonify({'scan_id':sid})

@app.route('/status/<sid>')
def status(sid):
    if sid not in scans: return jsonify({'error':'Not found'}),404
    s=scans[sid]
    return jsonify({k:s[k] for k in ['id','mode','phase','scanned','total_points','fine_scanned','total_fine_points','images_fetched','images_compared','no_coverage','matches','completed','error','results']})

@app.route('/cancel/<sid>', methods=['POST'])
def cancel(sid):
    if sid in scans: scans[sid]['cancelled']=True; return jsonify({'ok':True})
    return jsonify({'error':'Not found'}),404

@app.route('/export/<sid>')
def export(sid):
    if sid not in scans: return jsonify({'error':'Not found'}),404
    s=scans[sid]; out=io.StringIO(); w=csv.writer(out)
    w.writerow(['Lat','Lng','Score','Heading','Phase','pHash','SSIM','Histogram','Template','Link'])
    for r in s['results']:
        w.writerow([r['lat'],r['lng'],r['score'],r.get('heading',''),r['phase'],
            r['scores'].get('phash',''),r['scores'].get('ssim',''),r['scores'].get('histogram',''),
            r['scores'].get('template',''),r.get('sv_url') or r.get('gmaps','')])
    out.seek(0)
    return send_file(io.BytesIO(out.getvalue().encode()),mimetype='text/csv',as_attachment=True,download_name=f'geoscout_{sid}.csv')

if __name__=='__main__':
    print("\n  GeoScout v2.0 | Mapillary · Google SV · Baidu · Satellite")
    print("  http://localhost:5001\n")
    if getattr(sys, 'frozen', False):
        threading.Timer(1.5, lambda: __import__('webbrowser').open('http://localhost:5001')).start()
    app.run(host='0.0.0.0', port=5001, debug=False)
