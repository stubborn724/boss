"""ADB device control — screenshot, tap, swipe, input text.

All interactions with the Android device go through this module.
It wraps the ``adb`` command-line tool in subprocess calls.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any


def _find_adb() -> str:
	"""Locate the adb executable on the system PATH or common SDK locations."""
	adb = shutil.which("adb")
	if adb:
		return adb
	# Check common Android SDK locations on Windows
	for base in (os.environ.get("ANDROID_HOME", ""), os.environ.get("ANDROID_SDK_ROOT", ""),
				 os.path.expandvars(r"%LOCALAPPDATA%\Android\Sdk"),
				 r"C:\Android\Sdk", r"D:\Android\Sdk"):
		candidate = os.path.join(base, "platform-tools", "adb.exe") if base else ""
		if candidate and os.path.isfile(candidate):
			return candidate
	raise RuntimeError(
		"ADB 未找到。请安装 Android SDK Platform Tools 并确保 adb 在 PATH 中，"
		"或设置 ANDROID_HOME 环境变量。"
	)


def _run_adb(*args: str, timeout: int = 30) -> str:
	"""Run an adb command and return stdout. Raises RuntimeError on failure."""
	adb = _find_adb()
	cmd = [adb, *args]
	try:
		result = subprocess.run(
			cmd, capture_output=True, text=True, timeout=timeout,
			creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
		)
	except subprocess.TimeoutExpired:
		raise RuntimeError(f"ADB 命令超时 ({timeout}s): adb {' '.join(args)}")
	if result.returncode != 0:
		stderr = result.stderr.strip()
		# Some adb commands return non-zero but still produce valid output
		if stderr and "error" in stderr.lower():
			raise RuntimeError(f"ADB 错误: {stderr}")
	return result.stdout


class ADBController:
	"""Controls a single Android device via ADB.

	All coordinates are in **physical pixels** as returned by
	``adb shell wm size``. The caller is responsible for converting
	logical coordinates if needed.

	Usage::

		adb = ADBController()
		adb.wait_for_device()
		img = adb.screenshot()
		adb.tap(540, 960)
		adb.input_text("Hello")
	"""

	def __init__(self, serial: str | None = None) -> None:
		"""Connect to a device. If *serial* is None, uses the first USB device."""
		self._serial = serial
		self._screen_size: tuple[int, int] | None = None

	# ------------------------------------------------------------------
	# Device management
	# ------------------------------------------------------------------

	@property
	def serial(self) -> str:
		"""The device serial used for adb -s <serial>."""
		if self._serial is None:
			self._serial = self._first_device_serial()
		return self._serial

	@staticmethod
	def _first_device_serial() -> str:
		"""Return the serial of the first USB-connected device."""
		out = _run_adb("devices")
		for line in out.splitlines()[1:]:
			if not line.strip():
				continue
			parts = line.split()
			if len(parts) >= 2 and parts[1] == "device":
				return parts[0]
		raise RuntimeError(
			"未检测到 Android 设备。请通过 USB 连接手机并开启 USB 调试模式。"
		)

	@staticmethod
	def list_devices() -> list[dict[str, str]]:
		"""List all connected devices with their status."""
		out = _run_adb("devices", "-l")
		devices = []
		for line in out.splitlines()[1:]:
			parts = line.split()
			if len(parts) >= 2:
				devices.append({
					"serial": parts[0],
					"status": parts[1],
					"info": " ".join(parts[2:]) if len(parts) > 2 else "",
				})
		return devices

	def wait_for_device(self, timeout: int = 30) -> None:
		"""Block until the device is ready."""
		_run_adb("-s", self.serial, "wait-for-device", timeout=timeout)

	def is_screen_on(self) -> bool:
		"""Check whether the device screen is currently on."""
		out = _run_adb("-s", self.serial, "shell", "dumpsys power")
		# Different Android versions report differently
		return "mWakefulness=Awake" in out or "Display Power: state=ON" in out

	def wake_up(self) -> None:
		"""Wake the device screen and swipe to unlock (simple swipe)."""
		if not self.is_screen_on():
			_run_adb("-s", self.serial, "shell", "input keyevent 26")  # Power
			time.sleep(0.5)
		# Simple swipe up to dismiss lock screen (may not work with PIN/pattern)
		w, h = self.screen_size
		self.swipe(w // 2, int(h * 0.8), w // 2, int(h * 0.3), duration_ms=300)

	def press_back(self) -> None:
		"""Press the Android back button."""
		_run_adb("-s", self.serial, "shell", "input keyevent 4")

	def press_home(self) -> None:
		"""Press the Android home button."""
		_run_adb("-s", self.serial, "shell", "input keyevent 3")

	def press_enter(self) -> None:
		"""Press the Enter / search key."""
		_run_adb("-s", self.serial, "shell", "input keyevent 66")

	def launch_app(self, package: str, activity: str | None = None) -> None:
		"""Launch an Android app by package name."""
		if activity:
			cmd = f"am start -n {package}/{activity}"
		else:
			cmd = (
				f"monkey -p {package} -c android.intent.category.LAUNCHER 1"
			)
		_run_adb("-s", self.serial, "shell", cmd)

	def force_stop(self, package: str) -> None:
		"""Force-stop an app."""
		_run_adb("-s", self.serial, "shell", f"am force-stop {package}")

	# ------------------------------------------------------------------
	# Screen
	# ------------------------------------------------------------------

	@property
	def screen_size(self) -> tuple[int, int]:
		"""Physical screen size as (width, height)."""
		if self._screen_size is None:
			out = _run_adb("-s", self.serial, "shell", "wm size")
			# Output: "Physical size: 1080x2400"
			parts = out.strip().split()
			for token in parts:
				if "x" in token and token.count("x") == 1:
					w_str, h_str = token.split("x")
					try:
						self._screen_size = (int(w_str), int(h_str))
						break
					except ValueError:
						continue
			if self._screen_size is None:
				raise RuntimeError(f"无法解析屏幕尺寸: {out}")
		return self._screen_size

	def screenshot(self, path: str | None = None) -> bytes:
		"""Take a screenshot. If *path* is given, saves to that file.
		Returns PNG bytes.
		"""
		remote = "/sdcard/boss_agent_screenshot.png"
		_run_adb("-s", self.serial, "shell", "screencap", "-p", remote)
		if path:
			_run_adb("-s", self.serial, "pull", remote, path)
			return Path(path).read_bytes()
		with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
			local = tmp.name
		try:
			_run_adb("-s", self.serial, "pull", remote, local)
			return Path(local).read_bytes()
		finally:
			try:
				os.unlink(local)
			except OSError:
				pass

	# ------------------------------------------------------------------
	# Touch input
	# ------------------------------------------------------------------

	def tap(self, x: int, y: int) -> None:
		"""Tap at screen coordinates (x, y) in pixels."""
		_run_adb("-s", self.serial, "shell", "input", "tap", str(x), str(y))

	def long_press(self, x: int, y: int, duration_ms: int = 800) -> None:
		"""Long press at (x, y)."""
		_run_adb(
			"-s", self.serial, "shell", "input", "swipe",
			str(x), str(y), str(x), str(y), str(duration_ms),
		)

	def swipe(
		self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300,
	) -> None:
		"""Swipe from (x1, y1) to (x2, y2)."""
		_run_adb(
			"-s", self.serial, "shell", "input", "swipe",
			str(x1), str(y1), str(x2), str(y2), str(duration_ms),
		)

	def scroll_up(self, distance: int | None = None) -> None:
		"""Scroll up (finger moves up = content goes up)."""
		w, h = self.screen_size
		d = distance or int(h * 0.6)
		self.swipe(w // 2, int(h * 0.7), w // 2, int(h * 0.7) - d)

	def scroll_down(self, distance: int | None = None) -> None:
		"""Scroll down (finger moves down = content goes down)."""
		w, h = self.screen_size
		d = distance or int(h * 0.6)
		self.swipe(w // 2, int(h * 0.3), w // 2, int(h * 0.3) + d)

	# ------------------------------------------------------------------
	# Text input
	# ------------------------------------------------------------------

	def input_text(self, text: str) -> None:
		"""Type text into the currently focused field.

		Uses ADB input which sends key events — limited to ASCII characters.
		For Unicode (Chinese), falls back to clipboard paste via
		``adb shell input text`` with base64 encoding.
		"""
		# Try direct input first (works for ASCII)
		if text.isascii():
			# Escape special shell characters
			safe = text.replace(" ", "%s").replace("&", "\\&").replace("<", "\\<").replace(">", "\\>")
			_run_adb("-s", self.serial, "shell", "input", "text", safe)
		else:
			# For Chinese/Unicode, use the clipboard method
			# Encode as base64 and push via service call
			import base64
			encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
			_run_adb(
				"-s", self.serial, "shell",
				f"am broadcast -a ADB_INPUT_B64 --es msg {encoded}",
			)
			# Fallback: use ADB keyboard app or simply rely on the user
			# having a compatible input method. For now, log a warning.
			# Alternative approach: type character by character via keyevents
			# which is slow but works for many Chinese characters.
			try:
				# Try clipboard-based paste (Android 7+)
				_run_adb(
					"-s", self.serial, "shell",
					f"cmd clipboard set '{text}'",
				)
				time.sleep(0.3)
				# Paste
				self.long_press(*self._paste_coordinates())
			except Exception:
				pass

	def _paste_coordinates(self) -> tuple[int, int]:
		"""Estimate paste button location; overridden for specific apps."""
		# Default: center of input area (approximate)
		w, h = self.screen_size
		return (w // 2, int(h * 0.9))

	def clear_text(self, count: int = 100) -> None:
		"""Clear the current text field by pressing backspace repeatedly."""
		for _ in range(min(count, 10)):
			_run_adb("-s", self.serial, "shell", "input keyevent 67")
		if count > 10:
			# For longer clears, select all and delete
			self.long_press(*self._paste_coordinates())
			time.sleep(0.3)
			# Look for "select all" or just use the backspace approach

	# ------------------------------------------------------------------
	# App-specific helpers
	# ------------------------------------------------------------------

	def is_app_running(self, package: str) -> bool:
		"""Check if an app is currently in the foreground."""
		out = _run_adb("-s", self.serial, "shell", "dumpsys window")
		return package in out and "mCurrentFocus" in out


# Singleton for convenience
_default_controller: ADBController | None = None


def get_controller(serial: str | None = None) -> ADBController:
	"""Get or create the default ADB controller instance."""
	global _default_controller
	if _default_controller is None or (serial and serial != _default_controller.serial):
		_default_controller = ADBController(serial=serial)
	return _default_controller
