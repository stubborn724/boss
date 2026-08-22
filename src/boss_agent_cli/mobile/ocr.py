"""OCR-powered visual element detection.

Wraps PaddleOCR (or Tesseract as fallback) for Chinese text recognition
and provides element-locating utilities for the BOSS app automation.
"""

from __future__ import annotations

import io
import time
from typing import Any

from PIL import Image

from boss_agent_cli.mobile.screen import ScreenLayout, ScreenRegion


# ------------------------------------------------------------------
# OCR engine abstraction
# ------------------------------------------------------------------

class _PaddleOCRDriver:
	"""Lazy-loaded PaddleOCR wrapper for Chinese text recognition."""

	def __init__(self) -> None:
		self._ocr: Any = None

	def _ensure_loaded(self) -> None:
		if self._ocr is None:
			try:
				from paddleocr import PaddleOCR
				self._ocr = PaddleOCR(lang="ch", use_angle_cls=False, show_log=False)
			except ImportError:
				raise RuntimeError(
					"PaddleOCR 未安装。执行: pip install paddleocr"
				)

	def recognize(self, image_bytes: bytes) -> list[OCRBox]:
		"""Run OCR on an image and return detected text boxes."""
		self._ensure_loaded()
		img = Image.open(io.BytesIO(image_bytes))
		# PaddleOCR needs a file path or numpy array
		import numpy as np
		arr = np.array(img)
		raw = self._ocr.ocr(arr, cls=False)
		if raw is None or not raw:
			return []
		boxes: list[OCRBox] = []
		for line_group in raw:
			if line_group is None:
				continue
			for item in line_group:
				if item is None or len(item) < 2:
					continue
				bbox, (text, confidence) = item
				if not bbox or len(bbox) < 4:
					continue
				# bbox is [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
				x1 = (bbox[0][0] + bbox[3][0]) / 2
				y1 = (bbox[0][1] + bbox[1][1]) / 2
				x2 = (bbox[1][0] + bbox[2][0]) / 2
				y2 = (bbox[2][1] + bbox[3][1]) / 2
				boxes.append(OCRBox(
					text=text,
					confidence=confidence,
					x=int(x1), y=int(y1),
					w=int(x2 - x1), h=int(y2 - y1),
				))
		return boxes


class _TesseractDriver:
	"""Tesseract OCR fallback (requires tesseract-ocr + chi_sim language pack)."""

	def __init__(self) -> None:
		self._checked: bool = False

	def _ensure_loaded(self) -> None:
		if self._checked:
			return
		try:
			import pytesseract
			self._pytesseract = pytesseract
		except ImportError:
			raise RuntimeError(
				"Tesseract 未安装。执行: pip install pytesseract 并安装 tesseract-ocr"
			)
		self._checked = True

	def recognize(self, image_bytes: bytes) -> list[OCRBox]:
		self._ensure_loaded()
		img = Image.open(io.BytesIO(image_bytes))
		try:
			data = self._pytesseract.image_to_data(img, lang="chi_sim", output_type=self._pytesseract.Output.DICT)
		except Exception:
			# Fallback to English if Chinese language pack not installed
			data = self._pytesseract.image_to_data(img, output_type=self._pytesseract.Output.DICT)
		boxes: list[OCRBox] = []
		for i in range(len(data["text"])):
			text = (data["text"][i] or "").strip()
			if not text:
				continue
			conf = int(data["conf"][i]) if data["conf"][i] != "-1" else 0
			boxes.append(OCRBox(
				text=text,
				confidence=min(conf / 100.0, 1.0),
				x=data["left"][i], y=data["top"][i],
				w=data["width"][i], h=data["height"][i],
			))
		return boxes


class OCRBox:
	"""A single detected text region with position and confidence."""

	__slots__ = ("text", "confidence", "x", "y", "w", "h")

	def __init__(self, text: str, confidence: float, x: int, y: int, w: int, h: int) -> None:
		self.text = text
		self.confidence = confidence
		self.x = x
		self.y = y
		self.w = w
		self.h = h

	@property
	def center(self) -> tuple[int, int]:
		return (self.x + self.w // 2, self.y + self.h // 2)

	@property
	def region(self) -> ScreenRegion:
		"""Convert to ScreenRegion fractions (requires screen size context)."""
		return _box_to_region(self)

	def __repr__(self) -> str:
		return f'OCRBox("{self.text}", conf={self.confidence:.2f}, xy=({self.x},{self.y}))'


def _box_to_region(box: OCRBox) -> ScreenRegion:
	"""Convert OCRBox pixel coordinates to ScreenRegion fractions."""
	# Screen size will be injected by the caller
	raise NotImplementedError("Use OCRDriver.find_text() instead")


class OCRDriver:
	"""High-level OCR interface for the BOSS app automation.

	Usage::

		ocr = OCRDriver()
		screen_w, screen_h = 1080, 2400  # from ADBController
		layout = ScreenLayout(screen_w, screen_h)

		# Find text on screen
		hits = ocr.find_text(screenshot_bytes, "沟通")
		if hits:
			x, y = hits[0].center
			adb.tap(x, y)
	"""

	def __init__(self, engine: str = "auto") -> None:
		"""Initialize OCR driver.

		Args:
		  engine: "paddle", "tesseract", or "auto" (tries PaddleOCR first).
		"""
		self._engine: Any = None
		self._engine_name = engine

	def _get_engine(self):
		if self._engine is not None:
			return self._engine
		if self._engine_name == "tesseract":
			self._engine = _TesseractDriver()
		elif self._engine_name == "paddle":
			self._engine = _PaddleOCRDriver()
		else:  # auto
			try:
				self._engine = _PaddleOCRDriver()
				self._engine._ensure_loaded()
			except RuntimeError:
				self._engine = _TesseractDriver()
		return self._engine

	def recognize(self, image_bytes: bytes) -> list[OCRBox]:
		"""Run OCR on an image and return all detected text boxes."""
		engine = self._get_engine()
		t0 = time.monotonic()
		boxes = engine.recognize(image_bytes)
		elapsed = time.monotonic() - t0
		# Log timing but don't print during automation
		return boxes

	# ------------------------------------------------------------------
	# Element finding
	# ------------------------------------------------------------------

	def find_text(
		self,
		image_bytes: bytes,
		query: str,
		*,
		min_confidence: float = 0.5,
		region: ScreenRegion | None = None,
		screen_w: int = 1080, screen_h: int = 2400,
	) -> list[OCRBox]:
		"""Find all OCR boxes containing *query* text.

		Args:
		  image_bytes: PNG screenshot bytes.
		  query: Text to search for (substring match).
		  min_confidence: Minimum confidence (0.0-1.0).
		  region: If given, only search within this screen region.
		  screen_w, screen_h: Screen dimensions for region filtering.
		"""
		boxes = self.recognize(image_bytes)
		hits: list[OCRBox] = []
		for box in boxes:
			if query not in box.text:
				continue
			if box.confidence < min_confidence:
				continue
			if region is not None:
				px, py, pw, ph = region.to_pixels(screen_w, screen_h)
				if not (px <= box.x <= px + pw and py <= box.y <= py + ph):
					continue
			hits.append(box)
		return hits

	def find_text_exact(
		self, image_bytes: bytes, query: str, **kwargs: Any,
	) -> list[OCRBox]:
		"""Find exact (not substring) text matches."""
		boxes = self.find_text(image_bytes, query, **kwargs)
		return [b for b in boxes if b.text.strip() == query.strip()]

	def find_best(
		self,
		image_bytes: bytes,
		queries: list[str],
		**kwargs: Any,
	) -> OCRBox | None:
		"""Find the best match from a list of candidate queries.
		Returns the highest-confidence match across all queries.
		"""
		best: OCRBox | None = None
		for q in queries:
			hits = self.find_text(image_bytes, q, **kwargs)
			for h in hits:
				if best is None or h.confidence > best.confidence:
					best = h
		return best

	def read_text_in_region(
		self,
		image_bytes: bytes,
		region: ScreenRegion,
		screen_w: int = 1080, screen_h: int = 2400,
	) -> str:
		"""OCR all text within a region and return concatenated string."""
		boxes = self.recognize(image_bytes)
		px, py, pw, ph = region.to_pixels(screen_w, screen_h)
		texts: list[str] = []
		for box in boxes:
			if px <= box.x <= px + pw and py <= box.y <= py + ph:
				texts.append(box.text)
		return "\n".join(texts)

	def read_list_items(
		self,
		image_bytes: bytes,
		region: ScreenRegion,
		item_height: float,
		screen_w: int = 1080, screen_h: int = 2400,
	) -> list[str]:
		"""Read items from a vertical list by dividing into equal-height rows.

		Args:
		  image_bytes: PNG screenshot.
		  region: The list area.
		  item_height: Height of each item as screen fraction (e.g., 0.10).
		  screen_w, screen_h: Screen dimensions.
		"""
		boxes = self.recognize(image_bytes)
		px, py, pw, ph = region.to_pixels(screen_w, screen_h)
		item_h_px = int(item_height * screen_h)
		num_items = max(1, ph // item_h_px)

		items: list[str] = []
		for i in range(num_items):
			top = py + i * item_h_px
			bottom = min(top + item_h_px, py + ph)
			row_texts: list[str] = []
			for box in boxes:
				if px <= box.x <= px + pw and top <= box.y <= bottom:
					row_texts.append(box.text)
			if row_texts:
				items.append(" ".join(row_texts))
			else:
				items.append("")
		return items

	def has_text(
		self,
		image_bytes: bytes,
		query: str,
		**kwargs: Any,
	) -> bool:
		"""Quick check if *query* text exists on screen."""
		return len(self.find_text(image_bytes, query, **kwargs)) > 0

	def wait_for_text(
		self,
		controller: Any,  # ADBController
		query: str,
		*,
		timeout: float = 10.0,
		interval: float = 0.5,
		**kwargs: Any,
	) -> OCRBox | None:
		"""Poll the screen until *query* text appears. Returns the box or None."""
		deadline = time.monotonic() + timeout
		while time.monotonic() < deadline:
			img = controller.screenshot()
			hits = self.find_text(img, query, **kwargs)
			if hits:
				return hits[0]
			time.sleep(interval)
		return None
