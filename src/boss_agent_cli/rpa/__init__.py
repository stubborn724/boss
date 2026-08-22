"""RPA (Robotic Process Automation) client for BOSS Zhipin.

Instead of calling reverse-engineered internal APIs, the RPA client opens
the real BOSS web page in a Chrome browser and reads data from the DOM
using CSS selectors. All interactions use human-like mouse/keyboard
behavior to minimize account ban risk.
"""

from boss_agent_cli.rpa.boss_client import BossRPAClient
from boss_agent_cli.rpa.pages import find_element, find_all_elements, safe_text

__all__ = ["BossRPAClient", "find_element", "find_all_elements", "safe_text"]
