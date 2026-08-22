"""Fix send_message_by_friend, friend_detail, accept_attachment_share
to use _find_card_by_friend_id instead of friend_id - 1 index assumption."""
import re

with open(r'src\boss_agent_cli\rpa\boss_client.py', 'r', encoding='utf-8') as f:
    content = f.read()

fixes = 0

# Fix 1: send_message_by_friend - card click section
# Replace from "self.navigate_to(CHAT_PAGE)" thru the first click check
old_send_nav = '''\t\tself.navigate_to(CHAT_PAGE)
\t\tself.wait_loaded()
\t\tself.human_delay(1.0, 2.0)

\t\t# 1. 按序号点开第 friend_id 个对话卡片
\t\ttarget_idx = max(0, int(friend_id) - 1) if isinstance(friend_id, int) or str(friend_id).isdigit() else 0
\t\tclicked = self._eval(f"""
\t\t(() => {{
\t\t\tconst cards = document.querySelectorAll(
\t\t\t\t'.geek-item-wrap, [class*="chat-item"], [class*="conversation-item"], li[data-fid]'
\t\t\t);
\t\t\tif (cards.length > {target_idx}) {{
\t\t\t\tconst card = cards[{target_idx}];
\t\t\t\tcard.scrollIntoView({{block: 'center'}});
\t\t\t\tcard.click();
\t\t\t\treturn true;
\t\t\t}}
\t\t\treturn false;
\t\t}})()
\t\t""")

\t\tif not clicked:
\t\t\tself._log(f"[RPA] send_message: index {target_idx} not found")
\t\t\treturn {"code": -1, "message": f"未找到第{target_idx + 1}个会话"}

\t\tself.human_delay(2.0, 3.0)'''

new_send_nav = '''\t\tself._ensure_chat_page()
\t\tself.human_delay(0.5, 1.0)

\t\t# 1. 按 BOSS 真实 friendId 查找并点开对话卡片
\t\ttarget_idx = self._find_card_by_friend_id(friend_id)
\t\tif target_idx is None:
\t\t\tself._log(f"[RPA] send_message: friend_id {friend_id} not found")
\t\t\treturn {"code": -1, "message": f"未找到 friend_id={friend_id} 的会话"}

\t\tclicked = self._eval(f"""
\t\t(() => {{
\t\t\tconst cards = document.querySelectorAll('.geek-item-wrap');
\t\t\tif (cards.length > {target_idx}) {{
\t\t\t\tcards[{target_idx}].scrollIntoView({{block: 'center'}});
\t\t\t\tcards[{target_idx}].click();
\t\t\t\treturn true;
\t\t\t}}
\t\t\treturn false;
\t\t}})()
\t\t""")

\t\tif not clicked:
\t\t\treturn {"code": -1, "message": f"无法点击 friend_id={friend_id} 的会话"}

\t\tself.human_delay(2.0, 3.0)'''

if old_send_nav in content:
    content = content.replace(old_send_nav, new_send_nav, 1)
    fixes += 1
    print("Fix 1: send_message_by_friend - OK")
else:
    print("Fix 1: NOT FOUND")
    # Show what's actually there
    idx = content.find('target_idx = max(0, int(friend_id)')
    if idx >= 0:
        snippet = content[idx-50:idx+250]
        print(repr(snippet[:200]))

# Fix 2: friend_detail - use _find_card_by_friend_id
old_fd_nav = '''\t\tself.navigate_to(CHAT_PAGE)
\t\tself.wait_loaded()
\t\tself.human_delay(0.5, 1.0)

\t\tresult_list: list[dict[str, Any]] = []
\t\tfor fid in friend_ids:
\t\t\ttarget_idx = max(0, int(fid) - 1) if isinstance(fid, int) or str(fid).isdigit() else 0'''

new_fd_nav = '''\t\tself._ensure_chat_page()
\t\tself.human_delay(0.3, 0.6)

\t\tresult_list: list[dict[str, Any]] = []
\t\tfor fid in friend_ids:
\t\t\ttarget_idx = self._find_card_by_friend_id(fid)
\t\t\tif target_idx is None:
\t\t\t\tself._log(f"[RPA] friend_detail: friend_id {fid} not found, skipping")
\t\t\t\tcontinue'''

if old_fd_nav in content:
    content = content.replace(old_fd_nav, new_fd_nav, 1)
    fixes += 1
    print("Fix 2: friend_detail - OK")
else:
    print("Fix 2: NOT FOUND")

# Fix 3: accept_attachment_share - use _find_card_by_friend_id
old_accept_nav = '''\t\tself.navigate_to(CHAT_PAGE)
\t\tself.wait_loaded()
\t\tself.human_delay(1.0, 2.0)

\t\t# 1. 点开候选人对话
\t\ttarget_idx = max(0, int(friend_id) - 1) if isinstance(friend_id, int) or str(friend_id).isdigit() else 0
\t\tclicked = self._eval(f"""
\t\t(() => {{
\t\t\tconst cards = document.querySelectorAll(
\t\t\t\t'.geek-item-wrap, [class*="chat-item"], [class*="conversation-item"], li[data-fid]'
\t\t\t);
\t\t\tif (cards.length > {target_idx}) {{
\t\t\t\tcards[{target_idx}].scrollIntoView({{block: 'center'}});
\t\t\t\tcards[{target_idx}].click();
\t\t\t\treturn true;
\t\t\t}}
\t\t\treturn false;
\t\t}})()
\t\t""")

\t\tif not clicked:
\t\t\tself._log(f"[RPA] accept_attachment: index {target_idx} not found")
\t\t\treturn {"code": -1, "message": f"未找到第{target_idx + 1}个会话"}'''

new_accept_nav = '''\t\tself._ensure_chat_page()
\t\tself.human_delay(0.5, 1.0)

\t\t# 1. 按 BOSS 真实 friendId 查找并点开对话卡片
\t\ttarget_idx = self._find_card_by_friend_id(friend_id)
\t\tif target_idx is None:
\t\t\tself._log(f"[RPA] accept_attachment: friend_id {friend_id} not found")
\t\t\treturn {"code": -1, "message": f"未找到 friend_id={friend_id} 的会话"}

\t\tclicked = self._eval(f"""
\t\t(() => {{
\t\t\tconst cards = document.querySelectorAll('.geek-item-wrap');
\t\t\tif (cards.length > {target_idx}) {{
\t\t\t\tcards[{target_idx}].scrollIntoView({{block: 'center'}});
\t\t\t\tcards[{target_idx}].click();
\t\t\t\treturn true;
\t\t\t}}
\t\t\treturn false;
\t\t}})()
\t\t""")

\t\tif not clicked:
\t\t\treturn {"code": -1, "message": f"无法点击 friend_id={friend_id} 的会话"}'''

if old_accept_nav in content:
    content = content.replace(old_accept_nav, new_accept_nav, 1)
    fixes += 1
    print("Fix 3: accept_attachment_share - OK")
else:
    print("Fix 3: NOT FOUND")

with open(r'src\boss_agent_cli\rpa\boss_client.py', 'w', encoding='utf-8') as f:
    f.write(content)

print(f"\nTotal fixes applied: {fixes}/3")
