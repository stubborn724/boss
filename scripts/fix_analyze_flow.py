"""Simplify analyze_one: remove separate accept_attachment_share call,
   let download_attachment_via_ui handle everything."""
import re

with open(r'src\boss_agent_cli\commands\recruiter\communication_pipeline.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Find the accept_attachment_share call block and comment it out
# Pattern: from "try:\n                accept_result = self._platform.accept_attachment_share"
# to the "except Exception:\n                pass  # ..."

old_pattern = r'(            try:\n                accept_result = self\._platform\.accept_attachment_share\(friend_id\).*?except Exception:\n                pass  #[^\n]+\n)'
matches = list(re.finditer(old_pattern, content, re.DOTALL))
print('Found %d accept_attachment_share blocks' % len(matches))

if matches:
    for m in matches:
        old = m.group(1)
        # Replace with just downloading attachment directly
        new = '''            # 直接下载附件 PDF（内部自动点「同意」+「附件简历」按钮）
            try:
                dl_result = self._platform.download_attachment_via_ui(
                    friend_id=friend_id,
                    save_dir=str(self._data_dir / 'recruiter' / 'attachments'),
                )
                if self._platform.is_success(dl_result):
                    zp = dl_result.get('zpData', {})
                    att_path = zp.get('attachment_path', '')
                    if att_path and Path(att_path).exists():
                        step_result.attachment_downloaded = True
                        step_result.attachment_path = str(att_path)
                        step_result.resume_path = str(att_path)
                        accept_clicked = True
                        self._logger.info('pre_check', candidate_name, '附件PDF已下载: ' + Path(att_path).name)
                        print('[ANALYZE] 附件PDF已下载!', flush=True)
            except Exception:
                pass  # 附件下载失败，后面会兜底下载在线简历
'''
        content = content.replace(old, new, 1)
        print('Replaced block at offset', m.start())

    # Also remove the duplicate download_attachment_via_ui call (the old Phase 1b)
    # Find "if accept_clicked:" block and remove it
    old2_pattern = r'(            if accept_clicked:\n                try:\n                    self\._logger\.info.*?附件PDF已下载!.*?\n.*?except Exception as exc:\n                    self\._logger\.warn.*?\n)'
    matches2 = list(re.finditer(old2_pattern, content, re.DOTALL))
    print('Found %d accept_clicked blocks' % len(matches2))
    for m in matches2:
        content = content.replace(m.group(1), '', 1)
        print('Removed accept_clicked block')

    with open(r'src\boss_agent_cli\commands\recruiter\communication_pipeline.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('Done')
else:
    print('No blocks found - the pattern may have changed')
    # Show context
    idx = content.find('accept_attachment_share')
    if idx >= 0:
        print('Context around accept_attachment_share:')
        print(repr(content[idx-50:idx+200]))
