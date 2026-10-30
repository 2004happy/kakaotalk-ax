"""Refresh this Mac's own Kakao session using its saved auto-login key."""
import base64,hashlib,http.client,json,os,platform,plistlib,socket,sqlite3,tempfile,urllib.parse
from pathlib import Path
from cryptography.hazmat.primitives.ciphers import Cipher,algorithms,modes
from cryptography.hazmat.primitives.padding import PKCS7
from kakaotalk_reader import load_config,hashed_device_uuid,KAKAO_PLIST_PATH

def refresh_session():
 from kakaotalk_loco import LIVE_LOCO_DISABLED
 if LIVE_LOCO_DISABLED:
  return {'status':'disabled','reason':'desktop_session_conflict','loginAttempted':False}
 cfg=load_config();prefs=plistlib.loads(Path(KAKAO_PLIST_PATH).read_bytes())
 device=hashed_device_uuid(cfg['uuid']);email=prefs['Email']
 raw=base64.b64decode(prefs['Dfpr93S FDS zXCV'],validate=True)
 dec=Cipher(algorithms.AES(hashlib.sha256(cfg['uuid'].encode()).digest()),modes.CBC(raw[:16])).decryptor()
 padded=dec.update(raw[16:])+dec.finalize();unpad=PKCS7(128).unpadder();auth=unpad.update(padded)+unpad.finalize()
 if len(auth)!=64:raise ValueError('Unexpected auto-login key length')
 info=plistlib.loads(Path('/Applications/KakaoTalk.app/Contents/Info.plist').read_bytes());version=info['CFBundleShortVersionString']
 agent='KT/'+version+' Mc/'+platform.mac_ver()[0]+' ko'
 # Match the installed app's HTTP profile where available, without printing headers.
 cache=Path.home()/'Library/Containers/com.kakao.KakaoTalkMac/Data/Library/Caches/Cache.db'
 with sqlite3.connect(cache.as_uri()+'?mode=ro',uri=True,timeout=3) as db:
  for (blob,) in db.execute("SELECT request_object FROM cfurl_cache_blob_data WHERE instr(request_object,CAST('Authorization' AS BLOB))>0"):
   for h in plistlib.loads(blob).get('Array',[]):
    if isinstance(h,dict) and h.get('Authorization','').partition('-')[2]==device:
     agent=h.get('User-Agent',agent)
 xvc=hashlib.sha512(('YLLAS|'+email+'|'+device+'|GRAEB|'+agent).encode()).hexdigest()[:16]
 body=urllib.parse.urlencode({'email':email.lower(),'password':auth.hex(),'device_uuid':device,'device_name':socket.gethostname().split('.')[0],'permanent':'1','auto_login':'1','os_version':platform.mac_ver()[0]})
 headers={'A':'mac/'+version+'/ko','User-Agent':agent,'X-VC':xvc,'Content-Type':'application/x-www-form-urlencoded','Accept':'application/json','Accept-Language':'ko'}
 conn=http.client.HTTPSConnection('katalk.kakao.com',timeout=10)
 conn.request('POST','/mac/account/login.json',body=body,headers=headers)
 response=conn.getresponse();data=response.read(1048577);conn.close()
 if len(data)>1048576:raise ValueError('Response too large')
 decoded=json.loads(data)
 own=decoded.get('userId')==cfg['user_id']
 report={'httpStatus':response.status,'applicationStatus':decoded.get('status'),'responseFields':list(decoded)[:25],'ownAccountVerified':own,'accessTokenPresent':bool(decoded.get('access_token'))}
 if decoded.get('status')==0 and own and decoded.get('access_token'):
  value={'token':decoded['access_token'],'device':device,'uid':cfg['user_id'],'version':version,'refresh_token':decoded.get('refresh_token'),'user_agent':agent}
  target=Path.home()/'.config/kakaotalk-reader/loco-session.json';target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
  fd,tmp=tempfile.mkstemp(prefix='.loco-session-',dir=target.parent)
  try:
   with os.fdopen(fd,'w') as f:json.dump(value,f)
   os.chmod(tmp,0o600);os.replace(tmp,target)
  finally:
   if os.path.exists(tmp):os.unlink(tmp)
  report['savedPrivateSession']=True
 return report
if __name__=='__main__':
 try:print(json.dumps(refresh_session()))
 except Exception as e:print(json.dumps({'errorType':type(e).__name__}))
