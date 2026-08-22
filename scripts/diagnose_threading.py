# -*- coding: utf-8 -*-
"""Exact simulation of web console conversation list loading with threading."""
import sys, io, time, threading, traceback
from queue import Queue
from concurrent.futures import Future
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

print("=== Simulating web console threading ===")
print()

# -- Step 1: Create platform (same as web.py) --
from boss_agent_cli.rpa.boss_client import BossRPAClient
from boss_agent_cli.platforms.zhipin_recruiter import BossRecruiterPlatform
from boss_agent_cli.commands.recruiter.conversation_listing import (
    extract_non_empty_record_list, conversation_items_from_records,
)

client = BossRPAClient(cdp_url='http://127.0.0.1:9222')
platform = BossRecruiterPlatform(client)

def list_recent():
    resp = platform.friend_list(page=1)
    records = extract_non_empty_record_list(resp)
    if not records:
        zpdata = resp.get("zpData") or resp.get("data")
        if isinstance(zpdata, dict):
            records = extract_non_empty_record_list(zpdata)
    return conversation_items_from_records(records)

# -- Step 2: Simulate _SerialTaskRunner (same as runtime.py) --
print("2. Creating serial task runner...")
task_queue = Queue()
task_thread_started = False
task_error = None

def task_runner_loop():
    global task_error
    print("   [SERIAL-THREAD] Started", flush=True)
    while True:
        print("   [SERIAL-THREAD] Waiting for next task...", flush=True)
        operation, future = task_queue.get()
        print(f"   [SERIAL-THREAD] Got task: {getattr(operation, '__name__', 'lambda')}", flush=True)
        if not future.set_running_or_notify_cancel():
            continue
        try:
            operation()
            future.set_result(None)
            print(f"   [SERIAL-THREAD] Task completed OK", flush=True)
        except BaseException as exc:
            future.set_exception(exc)
            task_error = exc
            print(f"   [SERIAL-THREAD] Task FAILED: {exc}", flush=True)
            traceback.print_exc()

def submit_task(op, name="unnamed"):
    op.__name__ = name
    future = Future()
    global task_thread_started
    if not task_thread_started:
        task_thread_started = True
        t = threading.Thread(target=task_runner_loop, name="serial", daemon=True)
        t.start()
    task_queue.put((op, future))
    return future

# -- Step 3: Simulate platform lock --
platform_lock = threading.Lock()

# -- Step 4: Simulate _run_conversation_list --
print("3. Submitting conversation list task...")

conversation_state = {"state": "idle", "items": []}

def run_conversation_list():
    print("   [TASK] _run_conversation_list started", flush=True)
    try:
        print("   [TASK] Acquiring platform lock...", flush=True)
        with platform_lock:
            print("   [TASK] Calling list_recent()...", flush=True)
            recorded = list_recent()
        print(f"   [TASK] Got {len(recorded)} items", flush=True)

        items = []
        for item in recorded[:20]:
            fid = item.get("friend_id")
            if isinstance(fid, bool) or not isinstance(fid, int) or fid <= 0:
                continue
            items.append({
                "candidate_name": str(item.get("candidate_name") or "?"),
                "updated_at": str(item.get("updated_at") or "-"),
            })

        conversation_state["state"] = "succeeded"
        conversation_state["items"] = items
        print(f"   [TASK] State set to succeeded with {len(items)} items", flush=True)
    except Exception as exc:
        conversation_state["state"] = "failed"
        conversation_state["error"] = str(exc)
        print(f"   [TASK] FAILED: {exc}", flush=True)
        traceback.print_exc()

# Submit the task
future = submit_task(run_conversation_list, "conversation_list")

# -- Step 5: Poll until done (simulating frontend polling) --
print("4. Polling for results (simulating frontend)...")
for i in range(30):
    state = conversation_state["state"]
    item_count = len(conversation_state.get("items", []))
    print(f"   Poll {i+1}: state={state}, items={item_count}", flush=True)

    if state == "succeeded":
        print(f"\n   SUCCESS! Loaded {item_count} candidates.")
        for item in conversation_state["items"][:3]:
            print(f"   - {item['candidate_name']}")
        break
    elif state == "failed":
        print(f"\n   FAILED: {conversation_state.get('error', '?')}")
        break

    time.sleep(0.5)
else:
    print(f"\n   TIMEOUT: Task still running after 15 seconds!")
    print(f"   task_error = {task_error}")

if task_error:
    print(f"\n   Serial thread error: {task_error}")

print("\n=== Done ===")
