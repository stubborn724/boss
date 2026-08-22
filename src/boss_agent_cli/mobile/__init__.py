"""Mobile device automation via ADB + visual recognition.

This package provides a RecruiterPlatform implementation that controls a
BOSS Zhipin Android app through ADB (Android Debug Bridge). Instead of
calling reverse-engineered APIs, it takes screenshots, locates UI elements
with OCR, and simulates human touch interactions — dramatically reducing
account ban risk compared to API-level automation.
"""

from boss_agent_cli.mobile.adb_controller import ADBController
from boss_agent_cli.mobile.ocr import OCRDriver
from boss_agent_cli.mobile.screen import ScreenRegion, ScreenLayout

__all__ = ["ADBController", "OCRDriver", "ScreenRegion", "ScreenLayout"]
