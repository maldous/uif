"""Typed metadata boundary for existing native publishers. Never executes effects.
References are evidence locators, not verified grants, claims or admission tokens.
No credentials, source bodies, artifact bytes, commands, URLs or provider IDs enter.
"""
import copy,datetime,hashlib,json,re
SCHEMA='native-publication-handoff/1'
PROJECTS={'Cloud','UIF','OffMenu','Circular Quay','ANQR','CIX'}
ROUTES={
 'cloud_worker_assets':{'project':'Cloud','native_tools':['mcp__codex_apps__cloudflare_execute'],'capability':'selected dashboard publisher exercised; destination/artifact/authority must be freshly bound'},
 'cloud_site_source':{'project':'Cloud','native_tools':['mcp__codex_apps__sites_save_site_version','mcp__codex_apps__sites_deploy_site_version','mcp__codex_apps__sites_get_deployment_status'],'capability':'existing original Site native publisher; no replacement Site or provider migration'},
 'project_release':{'project':None,'native_tools':[],'capability':'not qualified: owner-selected release/source/artifact/destination/publisher contract required; no generic Foo deploy'}
}

def exact(obj,keys,label):
 if type(obj)is not dict or set(obj)!=set(keys):raise ValueError('Exact '+label+' fields required')

def ref(value):
 if type(value)is not str or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./:-]{0,159}',value):raise ValueError('Bounded opaque evidence reference required')
 return value

def sha(value,n):
 if type(value)is not str or not re.fullmatch('[0-9a-f]{'+str(n)+'}',value):raise ValueError('Full immutable digest required')
 return value

def catalog():return {'schema':SCHEMA,'routes':copy.deepcopy(ROUTES),'foo_publish_supported':False,'publication_executed':False}

def normalize(value):
 exact(value,['schema','project','route','operation_id','source','artifact','destination_ref','publisher_ref','evidence_refs'],'handoff')
 if value['schema']!=SCHEMA or value['project']not in PROJECTS or value['route']not in ROUTES:raise ValueError('Unsupported schema/project/route')
 route=ROUTES[value['route']]
 if route['project']is not None and route['project']!=value['project']:raise ValueError('Route/project mismatch')
 ref(value['operation_id']);ref(value['destination_ref']);ref(value['publisher_ref'])
 source=value['source'];exact(source,['kind','identity','tree'],'source')
 if source['kind']=='git':sha(source['identity'],40);sha(source['tree'],40)
 elif source['kind']=='generated_snapshot':sha(source['identity'],64);sha(source['tree'],64)
 else:raise ValueError('Unsupported source kind')
 artifact=value['artifact'];exact(artifact,['sha256','size_bytes'],'artifact');sha(artifact['sha256'],64)
 if type(artifact['size_bytes'])is not int or not 0<artifact['size_bytes']<=2147483648:raise ValueError('Positive bounded artifact size required')
 refs=value['evidence_refs']
 if type(refs)is not list or not 1<=len(refs)<=32 or len(set(refs))!=len(refs):raise ValueError('Distinct bounded evidence references required')
 for r in refs:ref(r)
 canonical=json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
 if len(canonical)>12000:raise ValueError('Handoff metadata too large')
 return {'schema':SCHEMA,'handoff_sha256':hashlib.sha256(canonical).hexdigest(),'handoff':copy.deepcopy(value),'native_tools':copy.deepcopy(route['native_tools']),'state':'native_verification_required'if route['native_tools']else'unsupported_release_route','publication_executed':False,'authority':'Metadata validity proves no permission, current claim, review acceptance, provider grant, artifact bytes or live state.'}

def bind_receipt(handoff,receipt):
 normalized=normalize(handoff)
 exact(receipt,['handoff_sha256','operation_id','destination_ref','artifact_sha256','native_receipt_ref','readback_ref','outcome'],'receipt')
 if receipt['handoff_sha256']!=normalized['handoff_sha256']or receipt['operation_id']!=handoff['operation_id']or receipt['destination_ref']!=handoff['destination_ref']or receipt['artifact_sha256']!=handoff['artifact']['sha256']:raise ValueError('Original source/artifact/destination/operation binding mismatch')
 ref(receipt['native_receipt_ref']);ref(receipt['readback_ref'])
 if receipt['outcome']not in ['succeeded','failed','unknown']:raise ValueError('Explicit terminal/unknown outcome required')
 if not normalized['native_tools']and receipt['outcome']=='succeeded':raise ValueError('Unsupported route cannot claim publisher success')
 return {'schema':SCHEMA,'handoff_sha256':normalized['handoff_sha256'],'receipt':copy.deepcopy(receipt),'state':'reported_'+receipt['outcome'],'independently_verified':False,'authority':'Caller must read original native operation and live postconditions. Binding validation is not a publication result or trusted attestation.'}

def main(action='catalog',handoff=None,receipt=None):
 if action=='catalog':return catalog()
 if action=='plan':return normalize(handoff)
 if action=='bind_receipt':return bind_receipt(handoff,receipt)
 raise ValueError('Unsupported action; no publish/deploy/credential/host execution action exists')
