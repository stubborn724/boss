r"""RPA — 精确点在线简历。用法: python scripts/test_rpa.py
"""

import sys, os, time, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def main():
    from boss_agent_cli.rpa.boss_client import BossRPAClient
    from boss_agent_cli.auth.persistent_chrome import PersistentChrome
    import httpx

    cdp_url = None
    for port in [9222, 9223]:
        try:
            r = httpx.get(f"http://127.0.0.1:{port}/json/version", timeout=2)
            if r.status_code == 200: cdp_url = f"http://127.0.0.1:{port}"; break
        except Exception: continue
    if not cdp_url:
        chrome = PersistentChrome(profile_dir=os.path.join(os.path.expanduser("~"), ".boss-agent", "chrome-profile"))
        cdp_url = chrome.ensure_running()

    input("\n请在 Chrome 中登录 BOSS，打开沟通页面，然后按 Enter...")

    client = BossRPAClient(cdp_url=cdp_url)
    client.ensure_session()
    ev = client._eval
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")

    # ====== 1. 读候选人列表 ======
    names = ev("""Array.from(document.querySelectorAll('.geek-name')).map(el => (el.textContent || '').trim()).filter(Boolean)""")
    jobs  = ev("""Array.from(document.querySelectorAll('.source-job')).map(el => (el.textContent || '').trim()).filter(Boolean)""")
    print(f"=== 读到 {len(names) if isinstance(names, list) else 0} 位候选人 ===")

    # ====== 2. 遍历候选人，逐个尝试打开简历 ======
    success_count = 0
    for idx in range(min(20, len(names) if isinstance(names, list) else 0)):
        name = names[idx]
        job = jobs[idx] if isinstance(jobs, list) and idx < len(jobs) else ''

        # 点候选人卡片
        ev(f"""
        (() => {{
            const items = document.querySelectorAll('.geek-item-wrap');
            if (items[{idx}]) {{
                items[{idx}].scrollIntoView({{block: 'center'}});
                items[{idx}].click();
            }}
        }})()
        """)
        time.sleep(1.5)

        # 找"在线简历"链接（href='', text='在线简历'的 a 标签）
        text_before = str(ev("(document.body || {}).innerText || ''"))

        clicked = ev("""
        (() => {
            const links = document.querySelectorAll('a');
            for (const a of links) {
                if ((a.textContent || '').trim() === '在线简历' && a.offsetParent) {
                    a.click(); return true;
                }
            }
            return false;
        })()
        """)

        if clicked:
            time.sleep(2)
            after = str(ev("(document.body || {}).innerText || ''"))
            if len(after) > len(text_before) + 100:
                diff = after[len(text_before):]
                # 保存
                safe_name = name.replace('/', '_').replace('\\', '_')
                path = os.path.join(desktop, f"BOSS_{idx+1}_{safe_name}_{job}.md")
                with open(path, "w", encoding="utf-8") as f:
                    f.write(diff)
                success_count += 1
                print(f"   ✅ #{idx+1} {name} {job} → {path} ({len(diff)}字)")
            else:
                print(f"   ⚠️ #{idx+1} {name} {job} 点击了但无弹窗内容")
        else:
            print(f"   ❌ #{idx+1} {name} {job} 没有在线简历按钮")

        # 回退，准备下一个
        ev("""(() => { const btn = document.querySelector('a[text="取消"], button:has-text("取消"), .close'); if (btn) btn.click(); })()""")
        time.sleep(0.5)

    print(f"\n=== 完成: 成功保存 {success_count} 份简历到桌面 ===")


if __name__ == "__main__":
    main()
