# -*- coding: utf-8 -*-
import sys, io, json, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from boss_agent_cli.rpa.boss_client import BossRPAClient

c = BossRPAClient(cdp_url='http://127.0.0.1:9222')

result = c.friend_list(page=1)
items = result.get('zpData', {}).get('friendList', [])
print('Found', len(items), 'candidates')
for i, item in enumerate(items[:5]):
    print('  [%d] %s fid=%s' % (i, item.get('name'), item.get('friendId')))

fid = items[0].get('friendId')
name = items[0].get('name')
print('\n=== Testing: %s (fid=%s) ===' % (name, fid))

target_idx = c._find_card_by_friend_id(fid)
print('Card index:', target_idx)
c._eval('document.querySelectorAll(".geek-item-wrap")[%d].click();' % target_idx)
time.sleep(2.5)

agree_result = c._eval('''
(function(){
    var all = document.querySelectorAll("button, span, div");
    for (var i = 0; i < all.length; i++) {
        var text = (all[i].textContent || "").trim();
        if (text === "同意" && all[i].offsetParent !== null) {
            all[i].click();
            return "clicked-agree";
        }
    }
    return "no-agree-button";
})()
''')
print('Agree button:', agree_result)
time.sleep(2)

chat_info = c._eval('''
(function(){
    var r = {agreeButton: "", fileCards: [], chatSnippets: []};
    var all = document.querySelectorAll("button, span, div");
    for (var i = 0; i < all.length; i++) {
        if ((all[i].textContent || "").trim() === "同意") {
            r.agreeButton = "still-present"; break;
        }
    }
    if (!r.agreeButton) r.agreeButton = "gone";

    var everything = document.querySelectorAll("*");
    for (var i = 0; i < everything.length; i++) {
        var el = everything[i];
        var cls = String(el.className || "");
        var text = (el.textContent || "").trim();
        if ((cls.indexOf("file") >= 0 || cls.indexOf("attach") >= 0 ||
             cls.indexOf("resume") >= 0 || text.indexOf(".pdf") >= 0) &&
            el.offsetParent !== null && text.length < 100) {
            r.fileCards.push({
                tag: el.tagName,
                cls: cls.slice(0, 60),
                text: text.slice(0, 60),
            });
        }
    }

    var panels = document.querySelectorAll('[class*="panel"], [class*="content"], [class*="main"]');
    for (var i = 0; i < panels.length; i++) {
        var p = panels[i];
        var t = (p.textContent || "").trim();
        if (p.offsetParent !== null && t.length > 50 && t.length < 1000) {
            r.chatSnippets.push({cls: String(p.className).slice(0, 60), text: t.slice(0, 300)});
        }
    }
    return JSON.stringify(r);
})()
''')
print('\nChat state:')
print(chat_info[:3000])
