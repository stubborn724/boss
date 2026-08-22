# -*- coding: utf-8 -*-
import sys, io, json, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from boss_agent_cli.rpa.boss_client import BossRPAClient
c = BossRPAClient(cdp_url='http://127.0.0.1:9222')

result = c.friend_list(page=1)
items = result.get('zpData', {}).get('friendList', [])

for item in items:
    if '李朝阳' in str(item.get('name', '')):
        fid = item.get('friendId')
        print('Found: %s (fid=%s)' % (item.get('name'), fid))
        break

idx = c._find_card_by_friend_id(fid)
print('Card idx:', idx)
c._eval('(function(){var c=document.querySelectorAll(".geek-item-wrap")[%d];if(c)c.click();})()' % idx)
time.sleep(2.5)

# Click agree
c._eval('(function(){var a=document.querySelectorAll("button,span,div");for(var i=0;i<a.length;i++){if((a[i].textContent||"").trim()==="同意"&&a[i].offsetParent!==null){a[i].click();return;}}})()')
time.sleep(1.5)

# Check file button
b = c._eval('(function(){var b=document.querySelector(".resume-btn-file");if(!b)return "no-btn";return JSON.stringify({disabled:b.classList.contains("disabled"),text:(b.textContent||"").trim()});})()')
print('File btn:', b)

# Click file button
c._eval('(function(){var b=document.querySelector(".resume-btn-file");if(b&&!b.classList.contains("disabled"))b.click();})()')
time.sleep(3)

# Check what happened
dom = c._eval('''(function(){
var r = {url: window.location.href, iframes: [], pdfLinks: [], modals: []};
var iframes = document.querySelectorAll("iframe");
for (var i = 0; i < iframes.length; i++) {
    var src = iframes[i].getAttribute("src") || "";
    r.iframes.push({src: src.slice(0,150), visible: iframes[i].offsetParent !== null});
}
var all = document.querySelectorAll("*");
for (var i = 0; i < all.length; i++) {
    var href = all[i].getAttribute("href") || all[i].getAttribute("src") || all[i].getAttribute("data") || "";
    if (href.indexOf(".pdf") >= 0) { r.pdfLinks.push({tag: all[i].tagName, url: href.slice(0,200)}); }
}
var modals = document.querySelectorAll("[class*=modal], [class*=dialog], [class*=preview], [class*=viewer], [class*=drawer]");
for (var i = 0; i < modals.length; i++) {
    var m = modals[i];
    if (m.offsetParent !== null) {
        r.modals.push({
            cls: String(m.className).slice(0,80),
            text: (m.textContent||"").trim().slice(0,300),
            nIframe: m.querySelectorAll("iframe").length,
            nCanvas: m.querySelectorAll("canvas").length,
            nImg: m.querySelectorAll("img").length,
            nDownloadBtn: m.querySelectorAll("[class*=download], [title*=下载]").length,
        });
    }
}
return JSON.stringify(r);
})()''')
print('\nDOM:')
print(dom[:5000])
