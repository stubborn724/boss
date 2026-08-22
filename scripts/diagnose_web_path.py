# -*- coding: utf-8 -*-
"""Diagnose the web console conversation list loading path.

This simulates the EXACT same call chain the web console uses:
BossRecruiterPlatform -> BossRPAClient.friend_list() -> extract_non_empty_record_list
"""
import json
import sys
import io
import traceback

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

print("=" * 60)
print("Simulating web console conversation list loading...")
print()

# Step 1: Create RPA client (same as web console)
print("1. Creating BossRPAClient...")
from boss_agent_cli.rpa.boss_client import BossRPAClient
client = BossRPAClient(cdp_url='http://127.0.0.1:9222')
print("   OK")

# Step 2: Wrap in platform (same as web console)
print("2. Creating BossRecruiterPlatform...")
from boss_agent_cli.platforms.zhipin_recruiter import BossRecruiterPlatform
platform = BossRecruiterPlatform(client)
print(f"   Platform: {platform.display_name}")

# Step 3: Call friend_list through platform (same as list_recent)
print("3. Calling platform.friend_list(page=1)...")
try:
    resp = platform.friend_list(page=1)
    print(f"   Response code: {resp.get('code')}")
    print(f"   is_success: {platform.is_success(resp)}")
except Exception as e:
    print(f"   FAILED: {e}")
    traceback.print_exc()
    sys.exit(1)

# Step 4: Extract records (same as list_recent)
print("4. Extracting records...")
from boss_agent_cli.commands.recruiter.conversation_listing import (
    extract_non_empty_record_list,
    conversation_items_from_records,
)

records = extract_non_empty_record_list(resp)
print(f"   Primary extraction: {len(records)} records")

if not records:
    zpdata = resp.get("zpData") or resp.get("data")
    if isinstance(zpdata, dict):
        records = extract_non_empty_record_list(zpdata)
    print(f"   After zpData fallback: {len(records)} records")

if not records:
    zpdata = platform.unwrap_data(resp) if platform.is_success(resp) else None
    if isinstance(zpdata, dict):
        records = extract_non_empty_record_list(zpdata)
    print(f"   After unwrap_data fallback: {len(records)} records")

if not records and isinstance(resp, dict):
    for key in ("friendList", "list", "result", "items", "friends"):
        candidate = resp.get(key)
        if isinstance(candidate, list):
            records = [item for item in candidate if isinstance(item, dict)]
            if records:
                print(f"   Found in key '{key}': {len(records)} records")
                break

# Step 5: Project to conversation items
print("5. Projecting to conversation items...")
try:
    items = conversation_items_from_records(records)
    print(f"   Items: {len(items)}")
    for item in items[:3]:
        print(f"   - {item.get('candidate_name', '?')} | {item.get('position', '?')} | friend_id={item.get('friend_id')}")
except Exception as e:
    print(f"   FAILED: {e}")
    traceback.print_exc()
    sys.exit(1)

print()
print("=" * 60)
print("ALL STEPS PASSED - The platform call chain is healthy.")
print()
print("If you still see '沟通列表读取失败' in the web console,")
print("the issue is likely one of:")
print("  1. operating_mode is not 'research' -> compliance blocked")
print("  2. Race condition / first-load timing issue")
print("  3. Check server console for the actual exception traceback")
print("=" * 60)
