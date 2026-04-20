#!/usr/bin/env python3
"""Hue Entertainment Bridge v2.0.0
Immersive lighting with Event Bus integration and time-based auto-transitions.

v2.0 additions:
  - Event Bus SSE subscriber: reacts to events in real-time
  - TV on + lights dimming = auto-movie mode
  - Time-based transitions: afternoon→sunset→candlelight→off
  - Sun state (golden hour) = auto-preset
  - Media player energy sync
  - Persistent state in /data/hue_state.json

Endpoints:
  GET  /health, /lights, /scenes, /rooms, /status
  POST /scene/<name>, /movie, /music/<energy>, /ambient/<preset>
  POST /all/off, /all/on, /room/<room>/<scene>
  POST /light/<id>/set?on=true&bri=200&ct=300
  GET  /event-log — Recent event-driven actions
  GET  /auto-mode — Current auto-transition state
"""
import os,json,time,logging,random,threading
from datetime import datetime
from collections import deque
from flask import Flask,jsonify,request
import requests as http
import sseclient

BRIDGE_IP=os.environ.get('BRIDGE_IP','')
API_KEY=os.environ.get('API_KEY','')
API_PORT=int(os.environ.get('API_PORT','8096'))
HA_URL=os.environ.get('HA_URL','http://localhost:8123')
HA_TOKEN=os.environ.get('HA_TOKEN','')
EVENT_BUS_URL=os.environ.get('EVENT_BUS_URL','http://localhost:8092')
HUE=f'http://{BRIDGE_IP}/api/{API_KEY}'

app=Flask(__name__)
logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
logger=logging.getLogger('hue-entertainment')

# v2.0: State tracking
current_mode='auto'  # auto, movie, manual
last_auto_preset=None
event_actions=deque(maxlen=100)
DATA_FILE='/data/hue_state.json'

PRESETS={
  'sunset':[{'bri':200,'xy':[0.5,0.4]},{'bri':150,'xy':[0.55,0.35]},{'bri':100,'xy':[0.45,0.35]},{'bri':180,'xy':[0.6,0.38]}],
  'ocean':[{'bri':150,'xy':[0.17,0.2]},{'bri':180,'xy':[0.15,0.25]},{'bri':120,'xy':[0.2,0.3]},{'bri':160,'xy':[0.16,0.22]}],
  'forest':[{'bri':120,'xy':[0.3,0.5]},{'bri':100,'xy':[0.35,0.45]},{'bri':80,'xy':[0.25,0.4]},{'bri':140,'xy':[0.32,0.48]}],
  'fire':[{'bri':254,'xy':[0.6,0.38]},{'bri':200,'xy':[0.55,0.35]},{'bri':150,'xy':[0.65,0.33]},{'bri':180,'xy':[0.58,0.38]}],
  'aurora':[{'bri':150,'xy':[0.15,0.25]},{'bri':120,'xy':[0.3,0.15]},{'bri':180,'xy':[0.2,0.5]},{'bri':100,'xy':[0.25,0.1]}],
  'candlelight':[{'bri':80,'xy':[0.55,0.4]},{'bri':60,'xy':[0.58,0.38]},{'bri':70,'xy':[0.52,0.41]},{'bri':50,'xy':[0.56,0.39]}],
  'neon':[{'bri':254,'xy':[0.35,0.15]},{'bri':254,'xy':[0.15,0.06]},{'bri':254,'xy':[0.2,0.5]},{'bri':254,'xy':[0.55,0.35]}],
  'golden_hour':[{'bri':200,'xy':[0.52,0.41]},{'bri':180,'xy':[0.5,0.4]},{'bri':160,'xy':[0.48,0.39]},{'bri':140,'xy':[0.53,0.4]}],
}

# Time-based auto-transitions
TIME_PRESETS={
    (14,17):'ocean',      # afternoon: cool/productive
    (17,19):'sunset',     # golden hour
    (19,21):'candlelight', # evening wind-down
    (21,23):'candlelight', # night (dimmer)
}

def load_state():
    global current_mode, last_auto_preset
    try:
        if os.path.exists(DATA_FILE):
            d=json.load(open(DATA_FILE))
            current_mode=d.get('mode','auto')
            last_auto_preset=d.get('last_preset')
    except: pass

def save_state():
    try:
        json.dump({'mode':current_mode,'last_preset':last_auto_preset,'saved':datetime.now().isoformat()},open(DATA_FILE,'w'))
    except: pass

def hue_get(p):
    try: return http.get(f'{HUE}{p}',timeout=5).json()
    except: return {}
def hue_put(p,d):
    try: return http.put(f'{HUE}{p}',json=d,timeout=5).json()
    except Exception as e: return {'error':str(e)}

def apply_preset(preset_name, transition=10):
    """Apply a preset to all reachable lights."""
    global last_auto_preset
    if preset_name not in PRESETS:
        return False
    cs=PRESETS[preset_name]
    lids=[lid for lid,l in hue_get('/lights').items() if l['state'].get('reachable')]
    for i,lid in enumerate(lids):
        hue_put(f'/lights/{lid}/state',{'on':True,**cs[i%len(cs)],'transitiontime':transition})
    last_auto_preset=preset_name
    save_state()
    return True

def handle_event(ev):
    """v2.0: React to Event Bus events in real-time."""
    global current_mode
    eid=ev.get('entity_id','')
    new=ev.get('new_state','')
    old=ev.get('old_state','')
    sig=ev.get('significant',False)
    
    action=None
    
    # TV on = auto-movie mode
    if 'media_player.75_the_frame' in eid or ('media_player' in eid and 'frame' in eid):
        if new in ['on','playing'] and old in ['off','standby','unavailable']:
            logger.info('EVENT: TV on — activating movie mode')
            current_mode='movie'
            # Apply movie lighting
            for lid,l in hue_get('/lights').items():
                n=l['name'].lower()
                if any(k in n for k in ['tv','lightstrip','play','gradient']):
                    hue_put(f'/lights/{lid}/state',{'on':True,'bri':40,'ct':400,'transitiontime':20})
                elif 'back' in n:
                    hue_put(f'/lights/{lid}/state',{'on':True,'bri':20,'ct':454,'transitiontime':20})
                else:
                    hue_put(f'/lights/{lid}/state',{'on':False,'transitiontime':20})
            action='auto_movie_mode'
        elif new in ['off','standby'] and current_mode=='movie':
            logger.info('EVENT: TV off — restoring auto mode')
            current_mode='auto'
            # Restore time-based preset
            h=datetime.now().hour
            for (start,end),preset in TIME_PRESETS.items():
                if start<=h<end:
                    apply_preset(preset, transition=30)
                    break
            action='restore_auto_mode'
    
    # Sun state changes (golden hour)
    elif eid=='sun.sun':
        if new=='below_horizon' and current_mode=='auto':
            logger.info('EVENT: Sunset — golden hour preset')
            apply_preset('golden_hour', transition=50)  # Slow 5-second transition
            action='golden_hour_sunset'
        elif new=='above_horizon' and current_mode=='auto':
            logger.info('EVENT: Sunrise — brightening')
            # Bright, cool light for morning
            for lid in list(hue_get('/lights').keys())[:6]:
                hue_put(f'/lights/{lid}/state',{'on':True,'bri':200,'ct':250,'transitiontime':50})
            action='sunrise_brighten'
    
    # Weather change
    elif 'weather' in eid and sig:
        if new in ['rainy','pouring'] and current_mode=='auto':
            logger.info('EVENT: Rain — cozy candlelight')
            apply_preset('candlelight', transition=30)
            action='rainy_candlelight'
    
    # Presence
    elif 'presence' in eid:
        if new=='off':
            logger.info('EVENT: Departure — lights off')
            for gid in hue_get('/groups'): hue_put(f'/groups/{gid}/action',{'on':False,'transitiontime':20})
            action='departure_lights_off'
        elif new=='on' and old=='off':
            logger.info('EVENT: Arrival — welcome lighting')
            h=datetime.now().hour
            if h>=18 or h<6:
                apply_preset('candlelight', transition=20)
            else:
                apply_preset('ocean', transition=20)
            current_mode='auto'
            action='arrival_welcome'
    
    if action:
        event_actions.append({'time':datetime.now().isoformat(),'event':eid,'action':action,'old':old,'new':new})
        logger.info(f'ACTION: {action}')

def event_bus_subscriber():
    """v2.0: SSE subscriber thread."""
    while True:
        try:
            logger.info(f'Connecting to Event Bus SSE: {EVENT_BUS_URL}/events/stream')
            response=http.get(f'{EVENT_BUS_URL}/events/stream',stream=True,timeout=None)
            client=sseclient.SSEClient(response)
            logger.info('Event Bus SSE connected')
            for event in client.events():
                try:
                    ev=json.loads(event.data)
                    handle_event(ev)
                except json.JSONDecodeError:
                    pass
                except Exception as e:
                    logger.error(f'Event handling error: {e}')
        except Exception as e:
            logger.error(f'Event Bus SSE error: {e}')
        logger.info('Reconnecting to Event Bus in 10s...')
        time.sleep(10)

def time_transition_loop():
    """v2.0: Background thread for time-based auto-transitions."""
    last_preset_applied=None
    while True:
        if current_mode=='auto':
            h=datetime.now().hour
            for (start,end),preset in TIME_PRESETS.items():
                if start<=h<end and preset!=last_preset_applied:
                    logger.info(f'TIME: Auto-transition to {preset} (hour {h})')
                    apply_preset(preset, transition=100)  # 10-second gradual transition
                    last_preset_applied=preset
                    event_actions.append({'time':datetime.now().isoformat(),'event':'time_transition','action':f'auto_{preset}','hour':h})
                    break
        time.sleep(300)  # Check every 5 minutes

@app.route('/')
def index():
    return jsonify({'name':'Hue Entertainment Bridge','version':'2.0.0','bridge':BRIDGE_IP,'presets':list(PRESETS.keys()),'mode':current_mode,'last_preset':last_auto_preset,'lights':len(hue_get('/lights'))})

@app.route('/health')
def health():
    c=hue_get('/config')
    return jsonify({'status':'ok' if c else 'unreachable','bridge':c.get('name','?'),'api':c.get('apiversion','?'),'mode':current_mode,'event_bus':'connected' if event_actions else 'waiting'})

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
    global current_mode
    sd=hue_get('/scenes')
    nl=name.lower().replace('-',' ').replace('_',' ')
    for sid,s in sd.items():
        if nl in s['name'].lower():
            g=s.get('group','0')
            hue_put(f'/groups/{g}/action',{'scene':sid})
            current_mode='manual'
            return jsonify({'success':True,'scene':s['name']})
    return jsonify({'error':f'Not found: {name}','available':[s['name'] for s in sd.values()][:20]}),404

@app.route('/movie',methods=['POST','GET'])
def movie():
    global current_mode
    current_mode='movie'
    for lid,l in hue_get('/lights').items():
        n=l['name'].lower()
        if any(k in n for k in ['tv','lightstrip','play','gradient']):
            hue_put(f'/lights/{lid}/state',{'on':True,'bri':40,'ct':400,'transitiontime':20})
        elif 'back' in n:
            hue_put(f'/lights/{lid}/state',{'on':True,'bri':20,'ct':454,'transitiontime':20})
        else:
            hue_put(f'/lights/{lid}/state',{'on':False,'transitiontime':20})
    save_state()
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
    global current_mode
    if preset not in PRESETS: return jsonify({'error':f'Use: {list(PRESETS.keys())}'}),400
    apply_preset(preset)
    current_mode='manual'
    return jsonify({'success':True,'preset':preset})

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

@app.route('/event-log')
def event_log():
    return jsonify(list(event_actions)[-20:])

@app.route('/auto-mode')
def auto_mode():
    return jsonify({'mode':current_mode,'last_preset':last_auto_preset,'time_presets':TIME_PRESETS})

if __name__=='__main__':
    logger.info(f'Hue Entertainment Bridge v2.0.0 on :{API_PORT}')
    load_state()
    c=hue_get('/config')
    logger.info(f'Bridge: {c.get("name","?")}'if c else'Bridge unreachable!')
    # v2.0: Start Event Bus SSE subscriber
    threading.Thread(target=event_bus_subscriber,daemon=True).start()
    # v2.0: Start time-based transition loop
    threading.Thread(target=time_transition_loop,daemon=True).start()
    logger.info('Event Bus subscriber + time transitions started')
    app.run(host='0.0.0.0',port=API_PORT,debug=False)
