#!/usr/bin/env python3
"""
GeoScout v3.0 - Street-Level Image Search + Camera Intelligence

Providers:
  - Mapillary (free, crowdsourced, global)
  - Google Street View (paid, best quality, no China)
  - Baidu Panorama (free-ish, best China coverage)
  - Satellite tiles (ESRI/Sentinel/Google overhead imagery)

Camera Sources:
  - OpenStreetMap/Overpass (mapped camera locations, free)
  - Shodan (internet-facing cameras, API key required)
  - Insecam (publicly accessible cameras, no key)

Geocoding:
  - Nominatim/OpenStreetMap (address/city/coordinate lookup, free)
"""

import os, io, json, math, time, hashlib, threading, uuid, csv, re, sys, webbrowser
from datetime import datetime
from flask import Flask, request, jsonify, render_template, send_from_directory, send_file
from PIL import Image, ImageOps
import numpy as np
import urllib.request
import urllib.parse

if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

TEMPLATE_DIR = os.path.join(APP_DIR, 'templates')
STATIC_DIR = os.path.join(APP_DIR, 'static')
UPLOAD_DIR = os.path.join(APP_DIR, 'uploads')
RESULT_DIR = os.path.join(APP_DIR, 'results')

app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024

@app.after_request
def apply_security_headers(resp):
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    resp.headers['X-Frame-Options'] = 'DENY'
    resp.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    resp.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
    resp.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob: https: http:; "
        "connect-src 'self' https:; "
        "font-src 'self' data:; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "frame-ancestors 'none'"
    )
    if request.is_secure or request.headers.get('X-Forwarded-Proto') == 'https':
        resp.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    return resp

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
        req = urllib.request.Request(url, headers={'User-Agent':'GeoScout/3.0'})
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

def save_clean_upload(file_storage, ext):
    raw = file_storage.read()
    if not raw:
        raise ValueError('Empty upload')

    uid = hashlib.md5(raw).hexdigest()[:8]
    fmt_map = {
        '.jpg': 'JPEG',
        '.jpeg': 'JPEG',
        '.png': 'PNG',
        '.webp': 'WEBP',
        '.bmp': 'BMP',
    }
    fmt = fmt_map[ext]
    img = Image.open(io.BytesIO(raw))
    img.load()
    img = ImageOps.exif_transpose(img)

    if fmt in ('JPEG', 'BMP'):
        img = img.convert('RGB')
    elif fmt == 'PNG' and img.mode not in ('RGB', 'RGBA', 'L', 'LA'):
        img = img.convert('RGBA')
    elif fmt == 'WEBP' and img.mode not in ('RGB', 'RGBA'):
        img = img.convert('RGBA' if 'A' in img.mode else 'RGB')

    filename = f"{uid}{ext}"
    path = os.path.join(UPLOAD_DIR, filename)
    save_args = {}
    if fmt == 'JPEG':
        save_args.update({'quality': 95, 'optimize': True})
    elif fmt == 'PNG':
        save_args.update({'optimize': True})
    elif fmt == 'WEBP':
        save_args.update({'quality': 95, 'method': 6})

    img.save(path, format=fmt, **save_args)
    with Image.open(path) as clean:
        width, height = clean.size
    return filename, width, height

# ── GEOCODING (Nominatim / OpenStreetMap) ────────────────────

def geocode_query(query):
    """Convert an address, city name, or coordinate pair to lat/lng with bounds."""
    query = query.strip()
    # Check for raw coordinates first (e.g. "41.8781, -87.6298" or "41.8781 -87.6298")
    coord = re.match(r'^(-?\d+\.?\d*)\s*[,\s]\s*(-?\d+\.?\d*)$', query)
    if coord:
        lat, lng = float(coord.group(1)), float(coord.group(2))
        d = 0.005  # ~500m box around point
        return {'lat':lat,'lng':lng,'found':True,'display':f'{lat:.5f}, {lng:.5f}',
                'bounds':{'south':lat-d,'west':lng-d,'north':lat+d,'east':lng+d}}
    # Otherwise hit Nominatim
    encoded = urllib.parse.quote(query)
    url = (f"https://nominatim.openstreetmap.org/search?q={encoded}"
           f"&format=json&limit=5&addressdetails=1")
    try:
        req = urllib.request.Request(url, headers={'User-Agent':'GeoScout/3.0','Accept-Language':'en'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            results = []
            for r in data:
                lat, lng = float(r['lat']), float(r['lon'])
                bb = r.get('boundingbox', [])
                if bb and len(bb) == 4:
                    bounds = {'south':float(bb[0]),'north':float(bb[1]),'west':float(bb[2]),'east':float(bb[3])}
                else:
                    bounds = {'south':lat-0.01,'west':lng-0.01,'north':lat+0.01,'east':lng+0.01}
                results.append({'lat':lat,'lng':lng,'found':True,
                    'display':r.get('display_name',''),'type':r.get('type',''),
                    'bounds':bounds})
            if results:
                return results[0] if len(results)==1 else {'results':results,'found':True}
    except: pass
    return {'found':False,'error':'Location not found. Try a different search.'}

# ── OSM CAMERAS (Overpass API) ───────────────────────────────

def osm_cameras(bounds):
    """Query OpenStreetMap for mapped surveillance camera locations."""
    bbox = f"{bounds['south']},{bounds['west']},{bounds['north']},{bounds['east']}"
    query = (f'[out:json][timeout:30];'
             f'(node["man_made"="surveillance"]({bbox});'
             f'way["man_made"="surveillance"]({bbox}););'
             f'out center body;')
    url = f"https://overpass-api.de/api/interpreter?data={urllib.parse.quote(query)}"
    try:
        req = urllib.request.Request(url, headers={'User-Agent':'GeoScout/3.0'})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            cameras = []
            for el in data.get('elements', []):
                lat = el.get('lat') or el.get('center',{}).get('lat')
                lng = el.get('lon') or el.get('center',{}).get('lon')
                if not lat or not lng: continue
                tags = el.get('tags', {})
                cameras.append({
                    'lat':lat, 'lng':lng, 'source':'osm',
                    'type': tags.get('surveillance:type', 'unknown'),
                    'zone': tags.get('surveillance:zone', 'unknown'),
                    'operator': tags.get('operator', ''),
                    'mount': tags.get('camera:mount', ''),
                    'direction': tags.get('camera:direction', ''),
                    'description': tags.get('description', ''),
                    'osm_id': el.get('id', ''),
                })
            return {'cameras':cameras,'count':len(cameras)}
    except Exception as e:
        return {'cameras':[],'count':0,'error':str(e)}

# ── SHODAN CAMERAS ───────────────────────────────────────────

def shodan_search(bounds, api_key, query_extra='', max_results=100):
    """Search Shodan for internet-facing cameras within bounds."""
    clat = (bounds['south']+bounds['north'])/2
    clng = (bounds['west']+bounds['east'])/2
    # Approximate radius in km from bounds
    lat_km = (bounds['north']-bounds['south'])*111
    lng_km = (bounds['east']-bounds['west'])*111*math.cos(math.radians(clat))
    radius = max(1, min(100, int(max(lat_km, lng_km)/2)))

    # Shodan search filters for cameras
    base_queries = [
        f'webcam geo:{clat},{clng},{radius}',
        f'camera geo:{clat},{clng},{radius}',
        f'"Server: IP Webcam" geo:{clat},{clng},{radius}',
    ]
    if query_extra:
        base_queries = [f'{query_extra} geo:{clat},{clng},{radius}']

    cameras = []
    seen_ips = set()

    for q in base_queries:
        if len(cameras) >= max_results: break
        encoded = urllib.parse.quote(q)
        url = f"https://api.shodan.io/shodan/host/search?key={api_key}&query={encoded}&minify=false"
        try:
            req = urllib.request.Request(url, headers={'User-Agent':'GeoScout/3.0'})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
                for match in data.get('matches', []):
                    loc = match.get('location', {})
                    ip = match.get('ip_str', '')
                    if not loc.get('latitude') or not loc.get('longitude'): continue
                    if ip in seen_ips: continue
                    seen_ips.add(ip)

                    port = match.get('port', 80)
                    ssl = 'ssl' in match
                    proto = 'https' if ssl else 'http'

                    cam = {
                        'lat': loc['latitude'], 'lng': loc['longitude'],
                        'source': 'shodan', 'ip': ip, 'port': port,
                        'org': match.get('org', ''),
                        'product': match.get('product', ''),
                        'os': match.get('os', ''),
                        'hostnames': match.get('hostnames', []),
                        'city': loc.get('city', ''),
                        'country': loc.get('country_name', ''),
                        'last_update': match.get('timestamp', ''),
                        'access_url': f"{proto}://{ip}:{port}",
                        'shodan_url': f"https://www.shodan.io/host/{ip}",
                    }
                    # Check for screenshot
                    if match.get('opts', {}).get('screenshot'):
                        cam['has_screenshot'] = True
                        cam['screenshot_data'] = match['opts']['screenshot'].get('data', '')
                        cam['screenshot_mime'] = match['opts']['screenshot'].get('mime', 'image/jpeg')

                    # Try to determine if it's an accessible camera stream
                    banner = match.get('data', '').lower()
                    if any(k in banner for k in ['mjpg','mjpeg','snapshot','video','stream','jpeg']):
                        cam['likely_stream'] = True
                        # Common snapshot paths
                        for path in ['/snapshot.jpg','/shot.jpg','/image.jpg','/mjpg/video.mjpg',
                                     '/video.mjpg','/cgi-bin/snapshot.cgi','/snap.jpg','/capture',
                                     '/jpg/image.jpg','/axis-cgi/jpg/image.cgi']:
                            cam.setdefault('try_urls', []).append(f"{proto}://{ip}:{port}{path}")
                    else:
                        cam['likely_stream'] = False

                    cameras.append(cam)
        except Exception as e:
            if 'Access denied' in str(e) or '401' in str(e):
                return {'cameras':[],'error':'Invalid Shodan API key.'}
            continue

    return {'cameras':cameras[:max_results],'count':len(cameras),'radius_km':radius}

# ── INSECAM ──────────────────────────────────────────────────

# Country codes used by Insecam
INSECAM_COUNTRIES = {
    'US':'United States','JP':'Japan','DE':'Germany','IT':'Italy',
    'FR':'France','RU':'Russia','KR':'South Korea','GB':'United Kingdom',
    'NL':'Netherlands','CZ':'Czech Republic','TR':'Turkey','ES':'Spain',
    'UA':'Ukraine','PL':'Poland','IN':'India','MX':'Mexico','BR':'Brazil',
    'CA':'Canada','AR':'Argentina','TW':'Taiwan','AT':'Austria','IL':'Israel',
    'BG':'Bulgaria','CH':'Switzerland','NO':'Norway','SE':'Sweden','RO':'Romania',
    'BE':'Belgium','VN':'Vietnam','CN':'China','TH':'Thailand','IR':'Iran',
    'FI':'Finland','IE':'Ireland','DK':'Denmark','HU':'Hungary','ZA':'South Africa',
    'CL':'Chile','PT':'Portugal','AU':'Australia','CO':'Colombia','MY':'Malaysia',
    'ID':'Indonesia','PH':'Philippines','SG':'Singapore','HK':'Hong Kong',
}

def insecam_search(country_code='', page=1):
    """Scrape Insecam for publicly accessible camera feeds by country."""
    cameras = []
    if not country_code:
        return {'cameras':[],'error':'Country code required.'}

    url = f"http://www.insecam.org/en/bycountry/{country_code}/?page={page}"
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept':'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Referer':'http://www.insecam.org/',
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode('utf-8', errors='ignore')

            # Extract total pages
            total_match = re.findall(r'pagenavigator\("[^"]*",\s*(\d+)', html)
            total_pages = int(total_match[0]) if total_match else 1

            # Extract camera image URLs (these contain the camera IPs)
            img_urls = re.findall(r'<img[^>]+src="(http[^"]+)"[^>]*>', html)
            # Filter to only camera feed URLs (not site assets)
            feed_urls = [u for u in img_urls if re.match(r'https?://\d+\.\d+\.\d+\.\d+', u)]

            # Extract camera detail page links
            cam_links = re.findall(r'<a[^>]+href="(/en/view/\d+/)"', html)

            # Extract coordinates if available (from inline scripts)
            coords = re.findall(r'setLatLng\(\[(-?\d+\.?\d*),\s*(-?\d+\.?\d*)\]\)', html)
            # Also try alternative coordinate patterns
            if not coords:
                coords = re.findall(r'latitude["\s:=]+(-?\d+\.?\d*)[^0-9]+longitude["\s:=]+(-?\d+\.?\d*)', html)

            for i, link in enumerate(cam_links):
                cam_id = re.search(r'/view/(\d+)/', link)
                cam = {
                    'source': 'insecam',
                    'cam_id': cam_id.group(1) if cam_id else '',
                    'page_url': f"http://www.insecam.org{link}",
                    'thumbnail': feed_urls[i] if i < len(feed_urls) else '',
                    'country': country_code,
                }
                # Extract IP from feed URL
                if cam['thumbnail']:
                    ip_match = re.match(r'https?://(\d+\.\d+\.\d+\.\d+)', cam['thumbnail'])
                    if ip_match:
                        cam['ip'] = ip_match.group(1)
                        cam['stream_url'] = cam['thumbnail']

                # Assign coordinates if available
                if i < len(coords):
                    cam['lat'] = float(coords[i][0])
                    cam['lng'] = float(coords[i][1])

                cameras.append(cam)

            return {'cameras':cameras,'count':len(cameras),
                    'page':page,'total_pages':total_pages,'country':country_code}
    except Exception as e:
        return {'cameras':[],'error':str(e)}

def insecam_camera_detail(cam_id):
    """Get detailed info for a single Insecam camera including coordinates."""
    url = f"http://www.insecam.org/en/view/{cam_id}/"
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer':'http://www.insecam.org/',
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode('utf-8', errors='ignore')
            result = {'cam_id': cam_id}

            # Coordinates
            lat = re.search(r'latitude["\s:=]+(-?\d+\.?\d*)', html)
            lng = re.search(r'longitude["\s:=]+(-?\d+\.?\d*)', html)
            if lat and lng:
                result['lat'] = float(lat.group(1))
                result['lng'] = float(lng.group(1))

            # Stream URL
            stream = re.search(r'(https?://\d+\.\d+\.\d+\.\d+[^"\'<>\s]*)', html)
            if stream:
                result['stream_url'] = stream.group(1)
                ip_match = re.match(r'https?://(\d+\.\d+\.\d+\.\d+)', result['stream_url'])
                if ip_match:
                    result['ip'] = ip_match.group(1)

            # Location info
            loc = re.findall(r'<div[^>]*>([^<]*(?:Country|City|Region|Timezone)[^<]*)</div>', html)
            for l in loc:
                if 'Country' in l: result['country_name'] = l.replace('Country:','').strip()
                if 'City' in l: result['city'] = l.replace('City:','').strip()
                if 'Region' in l: result['region'] = l.replace('Region:','').strip()

            return result
    except:
        return {'cam_id':cam_id,'error':'Could not fetch camera details.'}

# ── Camera feed comparison ───────────────────────────────────

def compare_camera_feed(ref_path, feed_url, weights):
    """Fetch a frame from a camera feed URL and compare against reference."""
    ref = Image.open(ref_path).convert('RGB')
    frame = fetch_image(feed_url, timeout=10)
    if not frame:
        return {'error':'Could not fetch camera frame.','url':feed_url}
    score, scores = compare_images(ref, frame, weights)
    return {
        'score': round(score*100, 1),
        'scores': {k:round(v*100,1) for k,v in scores.items()},
        'url': feed_url,
    }

# ── MAPILLARY (free) ─────────────────────────────────────────

def mly_query(bounds, token, limit=2000):
    bbox=f"{bounds['west']},{bounds['south']},{bounds['east']},{bounds['north']}"
    url=(f"https://graph.mapillary.com/images?access_token={token}"
         f"&fields=id,thumb_1024_url,thumb_256_url,computed_geometry,compass_angle,captured_at"
         f"&bbox={bbox}&limit={min(limit,2000)}")
    try:
        req=urllib.request.Request(url, headers={'User-Agent':'GeoScout/3.0'})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read()).get('data',[])
    except: return []

def mly_search(bounds, token):
    area = (bounds['east']-bounds['west'])*(bounds['north']-bounds['south'])
    if area <= 0.008:
        return mly_query(bounds, token)
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
            req=urllib.request.Request(f"https://maps.googleapis.com/maps/api/streetview/metadata?location={lat},{lng}&key={key}",headers={'User-Agent':'GeoScout/3.0'})
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
            req=urllib.request.Request(f"https://api.map.baidu.com/panorama/v2?ak={key}&width=64&height=32&location={bn},{bl}&fov=90",headers={'User-Agent':'GeoScout/3.0'})
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

@app.route('/uploads/<path:path>')
def serve_upload(path): return send_from_directory(UPLOAD_DIR, path)

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'image' not in request.files: return jsonify({'error':'No image'}),400
    f=request.files['image']
    if not f.filename: return jsonify({'error':'No file'}),400
    ext=os.path.splitext(f.filename)[1].lower()
    if ext not in ('.jpg','.jpeg','.png','.webp','.bmp'): return jsonify({'error':'Bad format'}),400
    try:
        fn, width, height = save_clean_upload(f, ext)
    except Exception:
        return jsonify({'error':'Could not process image'}),400
    return jsonify({
        'filename': fn,
        'width': width,
        'height': height,
        'metadata_stripped': True,
        'preview_url': f'/uploads/{fn}',
        'clean_download_url': f'/download/clean/{fn}',
    })

@app.route('/estimate', methods=['POST'])
def est():
    data = request.json or {}
    if not data.get('bounds'):
        return jsonify({'error':'No bounds provided.'}),400
    return jsonify(estimate(data))

@app.route('/scan', methods=['POST'])
def start():
    d=request.json or {}
    fn=d.get('filename')
    if not fn: return jsonify({'error':'No image'}),400
    if not d.get('bounds'): return jsonify({'error':'No bounds provided.'}),400
    mode=d.get('mode','mapillary')
    if mode in ('mapillary','google','baidu') and not d.get('api_key','').strip():
        return jsonify({'error':'API key/token required for this mode.'}),400
    fp=os.path.join(UPLOAD_DIR,fn)
    if not os.path.exists(fp): return jsonify({'error':'Not found'}),404
    ref=Image.open(fp).convert('RGB')
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
    if mode not in engines: return jsonify({'error':'Unsupported mode'}),400
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

@app.route('/download/clean/<filename>')
def download_clean(filename):
    path = os.path.join(UPLOAD_DIR, filename)
    if not os.path.exists(path): return jsonify({'error':'Not found'}),404
    return send_from_directory(UPLOAD_DIR, filename, as_attachment=True, download_name=f'geoscout_clean_{filename}')

# ── New v3 routes ────────────────────────────────────────────

@app.route('/geocode', methods=['POST'])
def geocode_route():
    d = request.json
    query = d.get('query', '').strip()
    if not query: return jsonify({'error':'No search query.'}),400
    return jsonify(geocode_query(query))

@app.route('/cameras/osm', methods=['POST'])
def cameras_osm():
    d = request.json
    bounds = d.get('bounds')
    if not bounds: return jsonify({'error':'No bounds provided.'}),400
    return jsonify(osm_cameras(bounds))

@app.route('/cameras/shodan', methods=['POST'])
def cameras_shodan():
    d = request.json
    bounds = d.get('bounds')
    api_key = d.get('api_key', '')
    if not bounds: return jsonify({'error':'No bounds provided.'}),400
    if not api_key: return jsonify({'error':'Shodan API key required.'}),400
    return jsonify(shodan_search(bounds, api_key, d.get('query',''), d.get('max_results',100)))

@app.route('/cameras/insecam', methods=['POST'])
def cameras_insecam():
    d = request.json
    country = d.get('country', '')
    page = d.get('page', 1)
    if not country: return jsonify({'error':'Country code required.'}),400
    return jsonify(insecam_search(country, page))

@app.route('/cameras/insecam/detail/<cam_id>')
def cameras_insecam_detail(cam_id):
    return jsonify(insecam_camera_detail(cam_id))

@app.route('/cameras/insecam/countries')
def cameras_insecam_countries():
    return jsonify(INSECAM_COUNTRIES)

@app.route('/cameras/compare', methods=['POST'])
def cameras_compare():
    d = request.json or {}
    fn = d.get('filename')
    feed_url = d.get('feed_url')
    if not fn or not feed_url: return jsonify({'error':'Need filename and feed_url.'}),400
    fp = os.path.join(UPLOAD_DIR, fn)
    if not os.path.exists(fp): return jsonify({'error':'Reference image not found.'}),404
    weights = {'phash':d.get('w_phash',30),'ssim':d.get('w_ssim',30),
               'histogram':d.get('w_histogram',20),'template':d.get('w_template',20)}
    return jsonify(compare_camera_feed(fp, feed_url, weights))

if __name__=='__main__':
    print("\n  GeoScout v3.0 | Mapillary · Google SV · Baidu · Satellite")
    print("  + Camera Intel: OSM · Shodan · Insecam")
    print("  + Geocoding: Address · City · Coordinates")
    print("  http://localhost:5001\n")
    if getattr(sys, 'frozen', False):
        threading.Timer(1.2, lambda: webbrowser.open('http://127.0.0.1:5001')).start()
    app.run(host='0.0.0.0', port=5001, debug=False)
