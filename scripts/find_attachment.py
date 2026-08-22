# -*- coding: utf-8 -*-
"""Manual test: download attachment PDF from BOSS chat."""
import sys, io, json, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from boss_agent_cli.rpa.boss_client import BossRPAClient

c = BossRPAClient(cdp_url='http://127.0.0.1:9222')

# Step 1: Get list
result = c.friend_list(page=1)
items = result.get('zpData', {}).get('friendList', [])
print(f'Found {len(items)} candidates')

# Step 2: Check exchange_content for first 15
print('\n=== Scanning ===')
for item in items[:15]:
    fid = item.get('friendId')
    name = item.get('name')
    url = '/wapi/zpboss/geek/exchangeContent?uid=' + str(fid)
    js_code = (
        '(async () => {'
        '  try {'
        '    const r = await fetch(' + json.dumps(url) + ', {credentials: "include"});'
        '    const d = await r.json();'
        '    const zp = (d && d.zpData) ? d.zpData : (d && d.data) ? d.data : {};'
        '    const keys = Object.keys(zp);'
        '    return JSON.stringify({fid:' + str(fid) + ', name:' + json.dumps(name) + ', keyCount: keys.length, sampleKeys: keys.slice(0,5)});'
        '  } catch(e) { return JSON.stringify({fid:' + str(fid) + ', name:' + json.dumps(name) + ', error: String(e).slice(0,60)}); }'
        '})()'
    )
    resp = c._eval(js_code, await_promise=True)
    try:
        parsed = json.loads(resp)
        kc = parsed.get('keyCount', 0)
        if kc > 0:
            print(f'  HAS DATA: {name} (fid={fid}) keys={parsed.get("sampleKeys", [])}')
        else:
            err = parsed.get('error', '')
            if err:
                print(f'  ERROR: {name} (fid={fid}) {err}')
    except Exception as exc:
        print(f'  PARSE FAIL: {name} {exc}')

print('\nDone')
