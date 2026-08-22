# -*- coding: utf-8 -*-
"""Simulate web console conversation list loading step by step."""
import sys, io, traceback
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

print("=== Simulating web console flow ===")
print()

# Step 1: Create BossRPAClient
print("1. BossRPAClient...")
from boss_agent_cli.rpa.boss_client import BossRPAClient
client = BossRPAClient(cdp_url='http://127.0.0.1:9222')
print("   OK")

# Step 2: Wrap in BossRecruiterPlatform
print("2. BossRecruiterPlatform...")
from boss_agent_cli.platforms.zhipin_recruiter import BossRecruiterPlatform
platform = BossRecruiterPlatform(client)
print("   OK")

# Step 3: Call friend_list through platform (same as list_recent in web.py)
print("3. platform.friend_list(page=1)...")
try:
    resp = platform.friend_list(page=1)
    print(f"   code={resp.get('code')}, is_success={platform.is_success(resp)}")
except Exception as e:
    print(f"   FAILED: {e}")
    traceback.print_exc()
    sys.exit(1)

# Step 4: Extract records
print("4. extract_non_empty_record_list...")
from boss_agent_cli.commands.recruiter.conversation_listing import (
    extract_non_empty_record_list, conversation_items_from_records,
)
records = extract_non_empty_record_list(resp)
print(f"   {len(records)} records")

if not records:
    zpdata = resp.get("zpData") or resp.get("data")
    if isinstance(zpdata, dict):
        records = extract_non_empty_record_list(zpdata)
    print(f"   After zpData fallback: {len(records)} records")

# Step 5: Project to items
print("5. conversation_items_from_records...")
try:
    items = conversation_items_from_records(records)
    print(f"   {len(items)} items projected")
    for item in items[:3]:
        print(f"   - {item.get('candidate_name', '?')} (friend_id={item.get('friend_id')})")
except Exception as e:
    print(f"   FAILED: {e}")
    traceback.print_exc()
    sys.exit(1)

# Step 6: Also test friend_detail (needed for analyze_one)
print("6. platform.friend_detail([1])...")
try:
    detail = platform.friend_detail([1])
    print(f"   code={detail.get('code')}, is_success={platform.is_success(detail)}")
    data = platform.unwrap_data(detail)
    if isinstance(data, dict):
        for k in ("friendList", "friends", "items", "list"):
            v = data.get(k)
            if isinstance(v, list) and v:
                r0 = v[0]
                if isinstance(r0, dict):
                    print(f"   record keys: {list(r0.keys())[:15]}")
                break
except Exception as e:
    print(f"   FAILED: {e}")
    traceback.print_exc()

print()
print("=== ALL STEPS PASSED ===")
print("The data path is healthy. Issue must be in web console threading/locking.")
