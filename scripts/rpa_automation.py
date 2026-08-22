r"""RPA 招聘闭环自动化 — BOSS 沟通列表批量处理.

用法: python scripts/rpa_automation.py

流程:
  1. 读取沟通列表所有候选人
  2. 遍历候选人，逐个：
     a) 打开聊天，读打招呼内容
     b) 有"在线简历"则截图OCR保存
     c) 基于岗位标准打分
     d) 标记不合适 / 保存简历 / 跳过
  3. 保存处理报告到桌面
"""

import sys, os, io, base64, time, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytesseract
pytesseract.pytesseract.tesseract_cmd = r'D:\Program Files\Tesseract-OCR\tesseract.exe'
from PIL import Image, ImageEnhance


# ================================================================
# 岗位标准（从需求文档提取）
# ================================================================
JOB_CRITERIA = {
    "Java开发实习生": {
        "must_have": ["Java", "Spring", "计算机相关专业", "实习"],
        "nice_to_have": ["项目经验", "MySQL", "Redis", "微服务", "AI编码"],
        "reject_if": ["非计算机专业"],
        "risk_signals": ["频繁跳槽"],
    },
    "售后技术支持": {
        "must_have": ["沟通能力"],
        "nice_to_have": ["计算机基础", "数据库", "相关经验"],
        "reject_if": [],
        "risk_signals": [],
    },
}


def score_candidate(resume_text: str, job_type: str) -> dict:
    """简单的关键词评分"""
    criteria = JOB_CRITERIA.get(job_type, JOB_CRITERIA.get("Java开发实习生", {}))
    text = (resume_text or "").lower().replace(" ", "")

    must_hits = [m for m in criteria.get("must_have", []) if m.lower().replace(" ", "") in text]
    nice_hits = [n for n in criteria.get("nice_to_have", []) if n.lower().replace(" ", "") in text]
    rejects = [r for r in criteria.get("reject_if", []) if r.lower().replace(" ", "") in text]
    risks = [r for r in criteria.get("risk_signals", []) if r.lower().replace(" ", "") in text]

    must_score = len(must_hits) / max(len(criteria.get("must_have", [])), 1)
    nice_score = len(nice_hits) / max(len(criteria.get("nice_to_have", [])), 1)
    total = int(must_score * 50 + nice_score * 40 + 10)

    action = "skip"
    if rejects:
        action = "reject"
    elif total >= 60:
        action = "save_and_contact"
    elif total >= 30:
        action = "save"

    return {
        "score": total,
        "must_hits": must_hits,
        "nice_hits": nice_hits,
        "rejects": rejects,
        "risks": risks,
        "action": action,
    }


def main():
    from boss_agent_cli.rpa.boss_client import BossRPAClient

    client = BossRPAClient(cdp_url="http://127.0.0.1:9222")
    client.ensure_session()
    ev = client._eval
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    report_dir = os.path.join(desktop, "BOSS_RPA_批处理")
    os.makedirs(report_dir, exist_ok=True)

    # ====== 1. 读取沟通列表 ======
    print("=" * 60)
    print("1. 读取沟通列表")
    client._eval('window.location.href = "https://www.zhipin.com/web/chat/index"')
    time.sleep(4)

    names = ev("""Array.from(document.querySelectorAll('.geek-name')).map(el => (el.textContent || '').trim()).filter(Boolean)""")
    jobs = ev("""Array.from(document.querySelectorAll('.source-job')).map(el => (el.textContent || '').trim()).filter(Boolean)""")
    times = ev("""Array.from(document.querySelectorAll('.time-shadow, .time')).map(el => (el.textContent || '').trim()).filter(Boolean)""")

    n = len(names) if isinstance(names, list) else 0
    print(f"   读取到 {n} 位候选人")
    for i in range(min(5, n)):
        j = jobs[i] if isinstance(jobs, list) and i < len(jobs) else ""
        t = times[i] if isinstance(times, list) and i < len(times) else ""
        print(f"   {i+1}. {names[i]} | {j} | {t}")

    # ====== 2. 遍历处理前 N 位 ======
    MAX_PROCESS = 10  # 先处理前 10 位
    results = []

    for idx in range(min(MAX_PROCESS, n)):
        name = names[idx]
        job = jobs[idx] if isinstance(jobs, list) and idx < len(jobs) else ""

        print(f"\n{'='*60}")
        print(f"[{idx+1}/{min(MAX_PROCESS, n)}] {name} | {job}")

        # a) 点候选人打开聊天
        ev(f"""(() => {{ const items = document.querySelectorAll('.geek-item-wrap'); if (items[{idx}]) {{ items[{idx}].click(); }} }})()""")
        time.sleep(2)

        # b) 读聊天内容（打招呼消息）
        chat_text = ev("""(() => { const msgs = document.querySelectorAll('.message-item, [class*=\"message\"], [class*=\"msg\"]'); const texts = []; for (const m of msgs) { const t = (m.textContent || '').trim(); if (t.length > 5) texts.push(t); } return texts.slice(-3).join(' | '); })()""")
        print(f"   打招呼: {chat_text[:150] if isinstance(chat_text, str) else 'N/A'}")

        # c) 找"在线简历"并 OCR
        resume_text = ""
        has_resume = ev("""(() => { const all = document.querySelectorAll('a'); for (const a of all) { if ((a.textContent||'').trim()==='在线简历' && a.offsetParent) return true; } return false; })()""")

        if has_resume:
            ev("""(() => { const all = document.querySelectorAll('a'); for (const a of all) { if ((a.textContent||'').trim()==='在线简历' && a.offsetParent) { a.click(); return 1; } } return 0; })()""")
            time.sleep(3)

            # 截图 iframe
            rect = ev("""(() => { const f = document.querySelector('.new-resume-online-main-ui iframe'); if (!f) return null; const r = f.getBoundingClientRect(); return {x:r.x, y:r.y, w:r.width, h:r.height}; })()""")
            if rect and isinstance(rect, dict) and rect.get('w', 0) > 100:
                r = client._cdp_send('Page.captureScreenshot', {
                    'format': 'png',
                    'clip': {'x': int(rect['x']), 'y': int(rect['y']), 'width': int(rect['w']), 'height': int(rect['h']), 'scale': 1}
                })
                img = Image.open(io.BytesIO(base64.b64decode(r['data'])))
                img = img.resize((img.width * 3, img.height * 3), Image.LANCZOS)
                img = ImageEnhance.Contrast(img).enhance(1.3)
                resume_text = pytesseract.image_to_string(img, lang='chi_sim', config='--psm 4')
                print(f"   简历OCR: {len(resume_text)} 字")

                # 保存简历
                safe_name = name.replace('/', '_').replace('\\', '_')
                resume_path = os.path.join(report_dir, f"{idx+1:02d}_{safe_name}_{job}_简历.md")
                with open(resume_path, 'w', encoding='utf-8') as f:
                    f.write(f"# {name} - {job}\n\n{resume_text}")
                print(f"   已保存: {resume_path}")

            # 关弹窗
            ev("""(() => { try { document.querySelector('.boss-dialog__close, [class*=\"close\"]').click(); } catch(e) {} })()""")
            time.sleep(1)
        else:
            print(f"   无在线简历")

        # d) 评分
        score = score_candidate(resume_text, job)
        print(f"   评分: {score['score']}分 | 命中: {score['must_hits']} | 动作: {score['action']}")

        # e) 根据评分执行操作
        if score['action'] == 'reject':
            # 点"不合适"
            ev("""(() => { const all = document.querySelectorAll('span, a, button, div'); for (const el of all) { const t = (el.textContent||'').trim(); if (t === '不合适' && el.offsetParent) { el.click(); return 1; } } return 0; })()""")
            time.sleep(1)
            # 选原因
            ev("""(() => { const all = document.querySelectorAll('span, label, div'); for (const el of all) { const t = (el.textContent||'').trim(); if ((t.includes('学历') || t.includes('经验') || t.includes('技能')) && el.offsetParent) { el.click(); return t; } } return ''; })()""")
            time.sleep(0.5)
            # 确认
            ev("""(() => { const all = document.querySelectorAll('span, button, a'); for (const el of all) { const t = (el.textContent||'').trim(); if ((t === '确定' || t === '提交') && el.offsetParent) { el.click(); return 1; } } return 0; })()""")
            print(f"   → 已标记不合适")
            time.sleep(1)

        results.append({
            "name": name, "job": job,
            "score": score["score"], "action": score["action"],
            "has_resume": bool(has_resume),
            "chat": chat_text[:200] if isinstance(chat_text, str) else "",
        })

    # ====== 3. 保存报告 ======
    report_path = os.path.join(report_dir, "处理报告.json")
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    summary_path = os.path.join(report_dir, "处理报告.md")
    with open(summary_path, 'w', encoding='utf-8') as f:
        f.write("# BOSS RPA 批处理报告\n\n")
        f.write(f"处理时间: {time.strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"共处理: {len(results)} 位候选人\n\n")
        f.write("| # | 候选人 | 职位 | 评分 | 动作 | 有简历 |\n")
        f.write("|---|--------|------|------|------|--------|\n")
        for i, r in enumerate(results):
            f.write(f"| {i+1} | {r['name']} | {r['job']} | {r['score']} | {r['action']} | {'✅' if r['has_resume'] else '❌'} |\n")

    print(f"\n{'='*60}")
    print(f"处理完成！")
    print(f"简历目录: {report_dir}")
    print(f"报告: {summary_path}")
    print(f"共处理 {len(results)} 位, 评分分布:")

    actions = {}
    for r in results:
        actions[r['action']] = actions.get(r['action'], 0) + 1
    for a, c in actions.items():
        label = {"reject": "不合适", "save": "保存待定", "save_and_contact": "保存并联系", "skip": "跳过"}.get(a, a)
        print(f"   {label}: {c} 人")


if __name__ == "__main__":
    main()
