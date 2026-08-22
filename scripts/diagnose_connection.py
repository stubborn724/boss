# -*- coding: utf-8 -*-
"""Diagnose BOSS connection issues."""
import json
import sys
import io

# Force UTF-8 output on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

print("=" * 50)
print("1. Check Chrome CDP connection...")

from boss_agent_cli.rpa.boss_client import BossRPAClient

client = BossRPAClient(cdp_url='http://127.0.0.1:9222')

try:
    client.ensure_session()
    print("   CDP connected OK")
except AttributeError as e:
    if '_log' in str(e):
        print("   CDP connected (minor: _log method missing in BossRPAClient)")
    else:
        print(f"   CDP connection FAILED: {e}")
        print()
        print("   -> Start Chrome with:")
        print('   "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --remote-debugging-port=9222')
        sys.exit(1)
except Exception as e:
    print(f"   CDP connection FAILED: {e}")
    print()
    print("   -> Start Chrome with:")
    print('   "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --remote-debugging-port=9222')
    sys.exit(1)

print()
print("2. Read conversation list...")

try:
    result = client.friend_list(page=1)
    code = result.get("code")
    print(f"   API code: {code}")
    items = result.get('zpData', {}).get('friendList', [])
    print(f"   Candidates found: {len(items)}")

    if len(items) == 0:
        print("   -> List is empty, possible causes:")
        print("     - BOSS DOM structure changed, selectors broken")
        print("     - Login expired, page redirected to login")
        print("     - No conversations in list")
    else:
        print("   First 3 candidates:")
        for item in items[:3]:
            print(f"     - {item.get('name', '?')} | {item.get('jobName', '?')} | {item.get('cityName', '?')}")

except PermissionError:
    print("   FAILED: BOSS login expired, please re-login in Chrome")
except Exception as e:
    print(f"   FAILED: {e}")
    import traceback
    traceback.print_exc()
    print()
    print("   Common causes:")
    print("   1. Login expired: visit https://www.zhipin.com/web/chat/index in Chrome")
    print("   2. DOM changed: BOSS updated CSS classes, selectors broken")
    print("   3. Network: proxy/VPN blocking requests")

print()
print("3. Check Chrome tabs...")
try:
    import urllib.request
    resp = urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=5)
    targets = json.loads(resp.read())
    boss_pages = [t for t in targets if 'zhipin.com' in t.get('url', '')]
    print(f"   Total tabs: {len(targets)}")
    print(f"   BOSS-related: {len(boss_pages)}")
    for t in boss_pages:
        print(f"     - {t['title'][:60]} | {t['url'][:80]}")
    if not boss_pages:
        print("   -> No BOSS page found! Open https://www.zhipin.com/web/chat/index in Chrome")
except Exception as e:
    print(f"   FAILED to read CDP targets: {e}")

print("=" * 50)
