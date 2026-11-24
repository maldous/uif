"""One-time deterministic import of reviewed v36 *geometry and artwork only*.

No reference fixtures, status values, sample text, or inferred assignments are
copied. Generated modules have no filesystem/runtime dependency on this script.
"""
from pathlib import Path
import json,base64,hashlib
ROOT=Path(__file__).resolve().parents[1]
REF=Path('/workspace/shared/environment-spirals-20261109')
BRANDS=Path('/workspace/shared/project-brand-assets')
d=json.loads((REF/'railway-v36/geometry-full.json').read_text())
labels=['Cloud','UIF','ANQR','CIX','OM','CQ']
projects=dict(zip('ABCDEF',labels))
cells=[]
for c in d['cells']:
 kind=c['kind']
 project=projects.get(c.get('session'))
 if kind=='group_summary': kind='summary'; ident='summary-'+project
 elif kind=='foo':
  kind='primary' if c['r']>=40 else 'overflow'
  peers=[v for v in d['cells'] if v.get('session')==c['session'] and v['kind']=='foo' and (v['r']>=40)==(c['r']>=40)]
  cx,cy=d['groups'][c['session']]['macro_center']
  import math
  peers.sort(key=lambda v:(math.atan2(v['y']-cy,v['x']-cx)+math.pi/2)%(2*math.pi))
  index=next(i for i,v in enumerate(peers,1) if v['id']==c['id'])
  ident=f'{project}-{kind}-{index}'
 elif kind=='cloud': kind='durable'; project=projects[c['label']];ident='durable-'+project
 elif kind=='morgan':kind='overall'; ident='overall'
 elif c.get('railway'):kind='railway';ident='railway-'+c['id'].rsplit('-',1)[1]
 else:raise ValueError(kind)
 out=dict(id=ident,kind=kind,project=project,x=c['x'],y=c['y'],r=c['r'],vertices=c['vertices'])
 if kind in ('primary','overflow'):out['index']=index
 if kind=='railway':
  import math
  a=(math.atan2(c['y'],c['x'])+math.pi/2)%(2*math.pi);i=int(math.floor(a/(math.pi/3)))%6
  out['projects']=[labels[i],labels[(i+1)%6]]
 cells.append(out)
groups=[]
for key,g in d['groups'].items():
 groups.append(dict(id=projects.get(key,key),center=g['macro_center'],rings=g['rings'],renderStroke=[v.get('render_stroke',True) for v in g['ring_metadata']]))
compact=json.loads((REF/'railway-v36/geometry-compact.json').read_text())
geom=dict(version='railway-v36',projects=labels,gap=d['parameters']['gap'],stroke=d['parameters']['group_stroke'],bounds=d['bounds'],compactBounds=compact['bounds'],cells=cells,groups=groups)
(ROOT/'public/lib/approved-geometry.js').write_text('/** Reviewed railway-v36 geometry only. No observed or illustrative state. */\nexport const APPROVED_GEOMETRY = '+json.dumps(geom,separators=(',',':'))+';\n')
files={
 'brandANQR':BRANDS/'anqr-logo.png',
 'brandOMLight':BRANDS/'offmenu-selected-balanced-final.png',
 'brandOMDark':BRANDS/'offmenu-selected-balanced-final-dark.png',
 'brandCQLight':BRANDS/'circularquay-logo-light.png',
 'brandCQDark':BRANDS/'circularquay-logo-dark.png',
 'fontRegular':REF/'fonts/Roboto-Regular.ttf',
 'fontSemibold':REF/'fonts/Roboto-SemiBold.ttf',
}
for name in ['tools','test','search','document','package','note','bug','puzzle','chart','compass','chat','hourglass','server']:
 files[name]=REF/f'solver-v28/emoji/{name}.png'
assets={key:'data:'+('font/ttf' if path.suffix=='.ttf' else 'image/png')+';base64,'+base64.b64encode(path.read_bytes()).decode() for key,path in files.items()}
from fontTools.ttLib import TTFont
font=TTFont(files['fontRegular']);units=font['head'].unitsPerEm
advances={chr(code):font['hmtx'][glyph][0]/units for code,glyph in font.getBestCmap().items()}
(ROOT/'public/lib/approved-assets.js').write_text('/** Approved local v36 artwork and Roboto fonts. No remote requests. */\nexport const APPROVED_ASSETS = Object.freeze('+json.dumps(assets,separators=(',',':'))+');\nexport const FONT_ADVANCES = Object.freeze('+json.dumps(advances,separators=(',',':'))+');\n')
manifest={key:{'file':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}for key,path in files.items()}
manifest['geometry-full.json']={'sha256':hashlib.sha256((REF/'railway-v36/geometry-full.json').read_bytes()).hexdigest()}
(ROOT/'scripts/approved-graph-provenance.json').write_text(json.dumps(manifest,indent=2)+'\n')
print('Generated exact geometry:',len(cells),'cells;',len(groups),'groups. Art assets:',len(assets))
