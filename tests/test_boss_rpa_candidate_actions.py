"""BOSS 候选人联系方式和约面试页面操作测试。"""

from boss_agent_cli.rpa.boss_client import BossRPAClient


def test_contact_exchange_opens_exact_friend_and_confirms_phone_request(monkeypatch) -> None:
	"""换电话必须先定位真实会话，再点击动作与二次确认，不能退回首个会话。"""
	client = object.__new__(BossRPAClient)
	calls: list[str] = []
	monkeypatch.setattr(client, "_open_conversation_for_candidate", lambda friend_id: calls.append(f"open:{friend_id}") or True, raising=False)
	monkeypatch.setattr(client, "_click_visible_candidate_action", lambda label: calls.append(f"action:{label}") or True, raising=False)
	monkeypatch.setattr(client, "_confirm_contact_exchange", lambda label: calls.append(f"confirm:{label}") or True, raising=False)

	result = client.request_contact_exchange(friend_id=42, contact_type="phone")

	assert result == {"code": 0, "zpData": {"friend_id": 42, "contact_type": "phone", "confirmed": True}}
	assert calls == ["open:42", "action:换电话", "confirm:换电话"]


def test_contact_exchange_stops_when_exact_friend_is_not_found(monkeypatch) -> None:
	"""会话定位失败时绝不能点击当前页面遗留的其它候选人按钮。"""
	client = object.__new__(BossRPAClient)
	monkeypatch.setattr(client, "_open_conversation_for_candidate", lambda _friend_id: False, raising=False)
	monkeypatch.setattr(client, "_click_visible_candidate_action", lambda _label: (_ for _ in ()).throw(AssertionError("不应继续点击")), raising=False)

	result = client.request_contact_exchange(friend_id=42, contact_type="wechat")

	assert result == {"code": -1, "message": "未找到 friend_id=42 的会话"}


def test_interview_invitation_uses_exact_friend_and_saved_payload(monkeypatch) -> None:
	"""约面试也需先定位会话，并把已验证的岗位配置完整交给表单填写器。"""
	client = object.__new__(BossRPAClient)
	calls: list[object] = []
	monkeypatch.setattr(client, "_open_conversation_for_candidate", lambda friend_id: calls.append(friend_id) or True, raising=False)
	monkeypatch.setattr(client, "_submit_interview_invitation", lambda payload: calls.append(payload) or True, raising=False)
	payload = {"mode": "online", "date": "2026-08-20", "time": "10:00", "note": "请提前进入会议"}

	result = client.invite_interview_via_ui(friend_id=42, payload=payload)

	assert result == {"code": 0, "zpData": {"friend_id": 42, "confirmed": True}}
	assert calls == [42, payload]
