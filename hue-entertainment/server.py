#!/usr/bin/env python3
"""Hue Entertainment Bridge v1.0.0
Immersive lighting: movie mode, music energy, ambient presets, room/scene control.

Endpoints:
  GET  /health, /lights, /scenes, /rooms, /status
  POST /scene/<name>, /movie, /music/<energy>, /ambient/<preset>
  POST /all/off, /all/on, /room/<room>/<scene>
  POST /light/<id>/set?on=true&bri=200&ct=300
"""
import os,json,time,logging,random
from flask import Flask,jsonify,request
import requests as http

BRIDGE_IP=os.environ.get('BRIDGE_IP','')
API_KEY=os.environ.get('API_KEY','')
API_PORT=int(os.environ.get('API_PORT','8096'))
HUE=f'http://{BRIDGE_IP}/api/{API_KEY}'

app=Flask(__name__)
logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
logger=logging.getLogger('hue-entertainment')

PRESETS={
  'sunset':[{'bri':200,'xy':[0.5,0.4]},{'bri':150,'xy':[0.55,0.35]},{'bri':100,'xy':[0.45,0.35]},{'bri':180,'xy':[0.6,0.38]}],
  'ocean':[{'bri':150,'xy':[0.17,0.2]},{'bri':180,'xy':[0.15,0.25]},{'bri':120,'xy':[0.2,0.3]},{'bri':160,'xy':[0.16,0.22]}],
  'forest':[{'bri':120,'xy':[0.3,0.5]},{'bri':100,'xy':[0.35,0.45]},{'bri':80,'xy':[0.25,0.4]},{'bri':140,'xy':[0.32,0.48]}],
  'fire':[{'bri':254,'xy':[0.6,0.38]},{'bri':200,'xy':[0.55,0.35]},{'bri':150,'xy':[0.65,0.33]},{'bri':180,'xy':[0.58,0.38]}],
  'aurora':[{'bri':150,'xy':[0.15,0.25]},{'bri':120,'xy':[0.3,0.15]},{'bri':180,'xy':[0.2,0.5]},{'bri':100,'xy':[0.25,0.1]}],
  'candlelight':[{'bri':80,'xy':[0.55,0.4]},{'bri':60,'xy':[0.58,0.38]},{'bri':70,'xy':[0.52,0.41]},{'bri':50,'xy':[0.56,0.39]}],
  'neon':[{'bri':254,'xy':[0.35,0.15]},{'bri':254,'xy':[0.15,0.06]},{'bri':254,'xy':[0.2,0.5]},{'bri':254,'xy':[0.55,0.35]}],
}

def hue_get(p):
    try: return http.get(f'{HUE}{p}',timeout=5).json()
    except: return {}
def hue_put(p,d):
    try: return http.put(f'{HUE}{p}',json=d,timeout=5).json()
    except Exception as e: return {'error':str(e)}

@app.route('/')
def index():
    return jsonify({'name':'Hue Entertainment Bridge','version':'1.0.0','bridge':BRIDGE_IP,'presets':list(PRESETS.keys()),'lights':len(hue_get('/lights'))})

@app.route('/health')
def health():
    c=hue_get('/config')
    return jsonify({'status':'ok' if c else 'unreachable','bridge':c.get('name','?'),'api':c.get('apiversion','?')})

@app.route('/lights')
def lights():
    d=hue_get('/lights')
    return jsonify([{'id':k,'name':v['name'],'on':v['state']['on'],'bri':v['state'].get('bri',0),'type':v['type'],'reachable':v['state'].get('reachable',False)} for k,v in d.items()])

@app.route('/scenes')
def scenes():
    d=hue_get('/scenes')
    return jsonify([{'id':k,'name':v['name'],'group':v.get('group','?')} for k,v in d.items()])

@app.route('/rooms')
def rooms():
    d=hue_get('/groups')
    return jsonify([{'id':k,'name':v['name'],'lights':v.get('lights',[]),'on':v['action'].get('on',False)} for k,v in d.items()])

@app.route('/status')
def status():
    d=hue_get('/lights')
    return jsonify({v['name']:{'on':v['state']['on'],'bri':v['state'].get('bri',0),'ct':v['state'].get('ct'),'reachable':v['state'].get('reachable')} for k,v in d.items()})

@app.route('/scene/<name>',methods=['POST','GET'])
def scene(name):
    sd=hue_get('/scenes')
    nl=name.lower().replace('-',' ').replace('_',' ')
    for sid,s in sd.items():
        if nl in s['name'].lower():
            g=s.get('group','0')
            hue_put(f'/groups/{g}/action',{'scene':sid})
            return jsonify({'success':True,'scene':s['name']})
    return jsonify({'error':f'Not found: {name}','available':[s['name'] for s in sd.values()][:20]}),404

@app.route('/movie',methods=['POST','GET'])
def movie():
    for lid,l in hue_get('/lights').items():
        n=l['name'].lower()
        if any(k in n for k in ['tv','lightstrip','play','gradient']):
            hue_put(f'/lights/{lid}/state',{'on':True,'bri':40,'ct':400,'transitiontime':20})
        elif 'back' in n:
            hue_put(f'/lights/{lid}/state',{'on':True,'bri':20,'ct':454,'transitiontime':20})
        else:
            hue_put(f'/lights/{lid}/state',{'on':False,'transitiontime':20})
    return jsonify({'success':True,'mode':'movie'})

@app.route('/music/<energy>',methods=['POST','GET'])
def music(energy):
    lids=list(hue_get('/lights').keys())
    if energy=='calm':
        for lid in lids: hue_put(f'/lights/{lid}/state',{'on':True,'bri':80,'ct':400,'transitiontime':10})
    elif energy=='medium':
        cs=PRESETS['sunset']
        for i,lid in enumerate(lids): hue_put(f'/lights/{lid}/state',{'on':True,**cs[i%len(cs)],'transitiontime':5})
    elif energy=='high':
        cs=PRESETS['neon']
        for i,lid in enumerate(lids): hue_put(f'/lights/{lid}/state',{'on':True,**cs[i%len(cs)],'transitiontime':2})
    else: return jsonify({'error':'Use: calm/medium/high'}),400
    return jsonify({'success':True,'energy':energy})

@app.route('/ambient/<preset>',methods=['POST','GET'])
def ambient(preset):
    if preset not in PRESETS: return jsonify({'error':f'Use: {list(PRESETS.keys())}'}),400
    cs=PRESETS[preset]
    lids=[lid for lid,l in hue_get('/lights').items() if l['state'].get('reachable')]
    for i,lid in enumerate(lids): hue_put(f'/lights/{lid}/state',{'on':True,**cs[i%len(cs)],'transitiontime':10})
    return jsonify({'success':True,'preset':preset,'lights':len(lids)})

@app.route('/all/off',methods=['POST','GET'])
def off():
    for gid in hue_get('/groups'): hue_put(f'/groups/{gid}/action',{'on':False})
    return jsonify({'success':True})

@app.route('/all/on',methods=['POST','GET'])
def on():
    for gid in hue_get('/groups'): hue_put(f'/groups/{gid}/action',{'on':True,'bri':254,'ct':250})
    return jsonify({'success':True})

@app.route('/room/<room>/<scene_name>',methods=['POST','GET'])
def room_scene(room,scene_name):
    groups=hue_get('/groups')
    rl=room.lower().replace('-',' ').replace('_',' ')
    gid=None
    for g,v in groups.items():
        if rl in v['name'].lower(): gid=g; break
    if not gid: return jsonify({'error':f'Room not found: {room}'}),404
    sd=hue_get('/scenes')
    sl=scene_name.lower().replace('-',' ').replace('_',' ')
    for sid,s in sd.items():
        if s.get('group')==gid and sl in s['name'].lower():
            hue_put(f'/groups/{gid}/action',{'scene':sid})
            return jsonify({'success':True,'room':room,'scene':s['name']})
    return jsonify({'error':f'Scene not found: {scene_name} in {room}'}),404

@app.route('/light/<lid>/set',methods=['POST','GET'])
def set_light(lid):
    s={}
    if request.args.get('on'): s['on']=request.args['on'].lower()=='true'
    if request.args.get('bri'): s['bri']=int(request.args['bri'])
    if request.args.get('ct'): s['ct']=int(request.args['ct'])
    if request.args.get('hue'): s['hue']=int(request.args['hue'])
    if request.args.get('sat'): s['sat']=int(request.args['sat'])
    if not s: return jsonify({'error':'Params: on,bri,ct,hue,sat'}),400
    return jsonify({'success':True,'result':hue_put(f'/lights/{lid}/state',s)})

if __name__=='__main__':
    logger.info(f'Hue Entertainment Bridge v1.0.0 on :{API_PORT}')
    c=hue_get('/config')
    logger.info(f'Bridge: {c.get("name","?")}'if c else'Bridge unreachable!')
    app.run(host='0.0.0.0',port=API_PORT,debug=False)
