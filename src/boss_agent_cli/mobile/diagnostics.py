"""Diagnostic checks for the Android mobile automation setup.

Usage:
    from boss_agent_cli.mobile.diagnostics import check_android_setup
    result = check_android_setup()
    if result["ready"]:
        print("Ready to use!")
"""

from __future__ import annotations

from typing import Any


def check_android_setup() -> dict[str, Any]:
	"""Run all Android mobile automation diagnostic checks.

	Returns a dict with ``ready`` (bool) and ``checks`` (list of results).
	"""
	checks = [
		_check_adb_available(),
		_check_device_connected(),
		_check_screenshot_capable(),
		_check_ocr_available(),
	]
	return {
		"ready": all(c["ok"] for c in checks),
		"checks": checks,
	}


def _check_adb_available() -> dict[str, Any]:
	"""Check if ADB binary is found on PATH."""
	try:
		from boss_agent_cli.mobile.adb_controller import _find_adb
		path = _find_adb()
		return {
			"name": "ADB 工具",
			"ok": True,
			"detail": f"已找到: {path}",
		}
	except RuntimeError as exc:
		return {
			"name": "ADB 工具",
			"ok": False,
			"detail": str(exc),
			"fix": (
				"下载 Android SDK Platform Tools: "
				"https://developer.android.com/tools/releases/platform-tools "
				"解压后将 platform-tools 目录加入 PATH 环境变量"
			),
		}


def _check_device_connected() -> dict[str, Any]:
	"""Check if at least one Android device is connected via ADB."""
	try:
		from boss_agent_cli.mobile.adb_controller import ADBController
		devices = ADBController.list_devices()
		if not devices:
			raise RuntimeError("未检测到设备")
		device_info = ", ".join(
			f"{d['serial']} ({d['status']})" for d in devices
		)
		return {
			"name": "设备连接",
			"ok": True,
			"detail": f"已连接 {len(devices)} 台设备: {device_info}",
		}
	except RuntimeError as exc:
		return {
			"name": "设备连接",
			"ok": False,
			"detail": str(exc),
			"fix": (
				"1. 手机通过 USB 连接电脑\n"
				"2. 手机上开启「开发者选项」→「USB 调试」\n"
				"3. 手机上允许此电脑的 USB 调试授权\n"
				"4. 运行 adb devices 确认设备列表中有 'device' 状态"
			),
		}


def _check_screenshot_capable() -> dict[str, Any]:
	"""Check if we can take a screenshot from the device."""
	try:
		from boss_agent_cli.mobile.adb_controller import ADBController
		adb = ADBController()
		adb.wait_for_device(timeout=10)
		img = adb.screenshot()
		if len(img) < 1000:  # Must be at least 1KB
			raise RuntimeError("截图数据过小")
		return {
			"name": "截图能力",
			"ok": True,
			"detail": f"截图成功 ({len(img)} bytes)",
		}
	except Exception as exc:
		return {
			"name": "截图能力",
			"ok": False,
			"detail": str(exc),
			"fix": "检查手机屏幕是否开启且未锁定",
		}


def _check_ocr_available() -> dict[str, Any]:
	"""Check if OCR engine is available."""
	for engine in ["paddle", "tesseract"]:
		try:
			from boss_agent_cli.mobile.ocr import OCRDriver
			ocr = OCRDriver(engine=engine)
			engine_obj = ocr._get_engine()
			# Try a simple OCR test with a blank image
			from PIL import Image
			import io
			img = Image.new("RGB", (200, 50), color="white")
			buf = io.BytesIO()
			img.save(buf, format="PNG")
			ocr.recognize(buf.getvalue())
			return {
				"name": "OCR 引擎",
				"ok": True,
				"detail": f"{engine} 就绪",
			}
		except Exception:
			continue
	return {
		"name": "OCR 引擎",
		"ok": False,
		"detail": "PaddleOCR 和 Tesseract 均不可用",
		"fix": (
			"安装 PaddleOCR: pip install paddlepaddle paddleocr\n"
			"或安装 Tesseract: 下载 tesseract-ocr-w64-setup.exe 并安装 chi_sim 语言包"
		),
	}


def quick_test() -> dict[str, Any]:
	"""Run a quick end-to-end test: screenshot + OCR the current screen.

	Call this when you want to verify the full pipeline works.
	"""
	result = check_android_setup()
	if not result["ready"]:
		return {"ok": False, "diagnostics": result}

	try:
		from boss_agent_cli.mobile.adb_controller import ADBController
		from boss_agent_cli.mobile.ocr import OCRDriver

		adb = ADBController()
		ocr = OCRDriver()

		w, h = adb.screen_size
		img = adb.screenshot()
		boxes = ocr.recognize(img)

		texts = [b.text for b in boxes[:20]]
		return {
			"ok": True,
			"screen": f"{w}x{h}",
			"screenshot_bytes": len(img),
			"ocr_boxes": len(boxes),
			"sample_texts": texts,
		}
	except Exception as exc:
		return {"ok": False, "error": str(exc), "diagnostics": result}
