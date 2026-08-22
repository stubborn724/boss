"""RPA 人机模拟层 — 让浏览器操作看起来像真人在用。

在现有 CDP/Playwright 基础上叠加人类行为特征：
- 贝塞尔曲线鼠标轨迹（非直线瞬移）
- 变速打字（非瞬间填充）
- 随机微停顿（非均匀间隔）
- 滚动变速（非固定步长）
- 操作前 hover（真人会先看再点）

用法：
    from boss_agent_cli.automation.humanize import HumanLikeBrowser

    bot = HumanLikeBrowser(page)
    await bot.click("#send-button")
    await bot.type("#message-input", "你好，看到您的简历...")
"""

from __future__ import annotations

import asyncio
import math
import random
import time
from typing import Any


# ------------------------------------------------------------------
# 行为参数 — 模拟普通上班族 HR 的操作节奏
# ------------------------------------------------------------------

class HumanBehaviorProfile:
	"""人类操作行为的可配置参数。

	不同角色可以有不同的行为模式，避免所有账号操作节奏一致。
	"""

	def __init__(
		self,
		typing_wpm: tuple[int, int] = (40, 65),    # 打字速度（字/分钟）
		click_delay: tuple[float, float] = (0.3, 1.2),  # 点击前后停顿（秒）
		scroll_pause: tuple[float, float] = (2.0, 6.0), # 滚动后阅读停顿
		action_gap: tuple[float, float] = (0.5, 3.0),  # 操作间间隔
		mouse_speed: tuple[float, float] = (0.3, 0.8),  # 鼠标移动速度因子
		hesitation_probability: float = 0.15,      # 偶尔犹豫一下的概率
		typo_probability: float = 0.02,            # 偶尔打错字的概率
		double_check_probability: float = 0.1,     # 偶尔回看上一段的概率
	):
		self.typing_wpm = typing_wpm
		self.click_delay = click_delay
		self.scroll_pause = scroll_pause
		self.action_gap = action_gap
		self.mouse_speed = mouse_speed
		self.hesitation_probability = hesitation_probability
		self.typo_probability = typo_probability
		self.double_check_probability = double_check_probability

	def random_typing_interval(self) -> float:
		"""返回输入两个字符之间的随机停顿（秒）。"""
		wpm = random.randint(*self.typing_wpm)
		base = 60.0 / (wpm * 5)  # 假设平均每字 5 个字符
		return base * random.uniform(0.5, 2.0)

	def random_click_delay(self) -> float:
		return random.uniform(*self.click_delay)

	def random_scroll_pause(self) -> float:
		return random.uniform(*self.scroll_pause)

	def random_gap(self) -> float:
		return random.uniform(*self.action_gap)

	def random_mouse_speed(self) -> float:
		return random.uniform(*self.mouse_speed)

	def should_hesitate(self) -> bool:
		return random.random() < self.hesitation_probability

	def should_make_typo(self) -> bool:
		return random.random() < self.typo_probability

	def should_double_check(self) -> bool:
		return random.random() < self.double_check_probability


# 预设角色
HR_JUNIOR = HumanBehaviorProfile(
	typing_wpm=(30, 50),          # 打字偏慢
	click_delay=(0.5, 1.5),
	scroll_pause=(3.0, 8.0),      # 看简历更仔细
	action_gap=(1.0, 4.0),
	mouse_speed=(0.2, 0.5),       # 鼠标偏慢
	hesitation_probability=0.2,     # 经常犹豫
)

HR_SENIOR = HumanBehaviorProfile(
	typing_wpm=(50, 75),          # 打字快
	click_delay=(0.2, 0.8),
	scroll_pause=(1.5, 4.0),      # 浏览快
	action_gap=(0.3, 2.0),
	mouse_speed=(0.4, 0.9),       # 鼠标快
	hesitation_probability=0.08,
)

HR_LAZY = HumanBehaviorProfile(
	typing_wpm=(35, 55),
	click_delay=(0.8, 2.5),       # 动作慢
	scroll_pause=(4.0, 12.0),     # 长时间停留
	action_gap=(2.0, 6.0),
	mouse_speed=(0.15, 0.4),
	hesitation_probability=0.3,
)


# ------------------------------------------------------------------
# 鼠标轨迹生成
# ------------------------------------------------------------------

def _bezier_curve(
	p0: tuple[float, float],
	p1: tuple[float, float],
	p2: tuple[float, float],
	p3: tuple[float, float],
	steps: int = 30,
) -> list[tuple[float, float]]:
	"""生成三次贝塞尔曲线路径点。"""
	points: list[tuple[float, float]] = []
	for i in range(steps + 1):
		t = i / steps
		x = ((1 - t) ** 3) * p0[0] + 3 * ((1 - t) ** 2) * t * p1[0] + 3 * (1 - t) * (t ** 2) * p2[0] + (t ** 3) * p3[0]
		y = ((1 - t) ** 3) * p0[1] + 3 * ((1 - t) ** 2) * t * p1[1] + 3 * (1 - t) * (t ** 2) * p2[1] + (t ** 3) * p3[1]
		points.append((x, y))
	return points


def generate_mouse_path(
	start: tuple[int, int],
	end: tuple[int, int],
	speed: float = 0.5,
) -> list[tuple[int, int]]:
	"""生成从起点到终点的人类鼠标移动路径。

	不是直线，而是带随机偏移的贝塞尔曲线，有加速减速过程。
	"""
	x1, y1 = start
	x2, y2 = end
	dx = abs(x2 - x1)
	dy = abs(y2 - y1)
	dist = math.sqrt(dx * dx + dy * dy)

	# 控制点加上随机偏移，让曲线弯曲
	jitter_x = dx * random.uniform(0.1, 0.3) * random.choice([-1, 1])
	jitter_y = dy * random.uniform(0.1, 0.3) * random.choice([-1, 1])

	# 贝塞尔控制点
	cp1 = (x1 + dx * 0.3 + jitter_x * 0.5, y1 + dy * 0.3 + jitter_y * 0.5)
	cp2 = (x1 + dx * 0.7 - jitter_x * 0.3, y1 + dy * 0.7 - jitter_y * 0.3)

	# 步数取决于距离和速度
	steps = max(15, int(dist / (speed * 50)))

	raw = _bezier_curve(
		(float(x1), float(y1)),
		cp1,
		cp2,
		(float(x2), float(y2)),
		steps=min(steps, 60),
	)

	# 添加微小的手抖偏移
	result: list[tuple[int, int]] = []
	for i, (x, y) in enumerate(raw):
		# 加速-减速：路径中段速度更快（点更稀疏）
		# 随机微抖（1-3 像素）
		shake = random.randint(-2, 2) if random.random() < 0.3 else 0
		result.append((int(x) + shake, int(y) + shake))

	return result


# ------------------------------------------------------------------
# 变速打字
# ------------------------------------------------------------------

def generate_typing_sequence(
	text: str,
	profile: HumanBehaviorProfile = HR_SENIOR,
) -> list[tuple[str, float]]:
	"""生成带时间间隔的输入序列。

	返回 [(要输入的字符或退格, 间隔秒数), ...]
	偶尔会有打错 → 退格 → 重打的模式。
	"""
	result: list[tuple[str, float]] = []
	i = 0
	while i < len(text):
		ch = text[i]

		# 偶尔打错字
		if profile.should_make_typo() and ch.isalpha():
			# 打一个邻近键位的错误字符
			nearby = _nearby_key(ch)
			if nearby:
				result.append((nearby, profile.random_typing_interval()))
				result.append(("\b", random.uniform(0.1, 0.3)))
				result.append((ch, profile.random_typing_interval()))
				i += 1
				continue

		interval = profile.random_typing_interval()

		# 在词边界多停一会（模拟思考）
		if ch in (" ", "，", "。", "！", "？", "\n") and random.random() < 0.4:
			interval *= random.uniform(1.5, 3.0)

		# 长词中间偶尔停顿
		if len(text) - i > 10 and random.random() < 0.05:
			interval += random.uniform(0.5, 1.5)

		result.append((ch, interval))
		i += 1

	return result


def _nearby_key(ch: str) -> str | None:
	"""返回键盘上邻近的字符（模拟打错字）。"""
	qwerty_nearby: dict[str, str] = {
		"a": "s", "b": "n", "c": "v", "d": "f", "e": "r",
		"f": "g", "g": "h", "h": "j", "i": "o", "j": "k",
		"k": "l", "l": "k", "m": "n", "n": "m", "o": "i",
		"p": "o", "q": "w", "r": "t", "s": "d", "t": "y",
		"u": "i", "v": "c", "w": "e", "x": "c", "y": "u",
		"z": "x",
	}
	return qwerty_nearby.get(ch.lower(), None)


# ------------------------------------------------------------------
# 滚动模拟
# ------------------------------------------------------------------

def generate_scroll_sequence(
	distance_px: int,
	profile: HumanBehaviorProfile = HR_SENIOR,
) -> list[tuple[int, float]]:
	"""生成分批滚动序列 [(每次滚动像素, 停顿秒数), ...]。

	真人不会一口气滚到底，而是滚一段、看一段。
	"""
	if distance_px <= 0:
		return []

	# 每次滚动大约 200-600 像素，不均匀
	segments: list[tuple[int, float]] = []
	remaining = distance_px
	while remaining > 0:
		step_max = min(600, remaining)
		step_min = min(200, step_max)
		step = remaining if step_min >= step_max else random.randint(step_min, step_max)
		pause = profile.random_scroll_pause()
		segments.append((step, pause))
		remaining -= step

		# 偶尔回滚一小段（看漏了往回找）
		if profile.should_double_check() and remaining > 300:
			back = random.randint(50, 150)
			segments.append((-back, random.uniform(1.0, 2.5)))
			remaining += back

	return segments


# ------------------------------------------------------------------
# Playwright 集成 — HumanLikeBrowser
# ------------------------------------------------------------------

class HumanLikeBrowser:
	"""在 Playwright Page 上叠加人类操作模拟。

	用法::

		bot = HumanLikeBrowser(page, profile=HR_SENIOR)
		await bot.click("button:has-text('发送')")
		await bot.type("textarea", "您好，看到您的简历...")
		await bot.scroll(400)
		await bot.reading_pause()  # 模拟阅读停顿
	"""

	def __init__(
		self,
		page: Any,  # playwright.async_api.Page
		profile: HumanBehaviorProfile = HR_SENIOR,
	):
		self._page = page
		self._profile = profile
		self._last_action_at = time.monotonic()
		self._viewport_w: int | None = None
		self._viewport_h: int | None = None

	async def _ensure_viewport(self) -> None:
		if self._viewport_w is None:
			vp = self._page.viewport_size
			if vp:
				self._viewport_w = vp["width"]
				self._viewport_h = vp["height"]
			else:
				self._viewport_w = 1920
				self._viewport_h = 1080

	async def _natural_gap(self) -> None:
		"""在操作之间插入自然的随机间隔。"""
		now = time.monotonic()
		elapsed = now - self._last_action_at
		min_gap = 0.5  # 最少也要有半秒
		if elapsed < min_gap:
			await asyncio.sleep(min_gap - elapsed + self._profile.random_gap())
		else:
			if self._profile.should_hesitate():
				await asyncio.sleep(self._profile.random_gap() * random.uniform(1.5, 3.0))
		self._last_action_at = time.monotonic()

	async def move_mouse_to(
		self,
		x: int, y: int,
		from_current: bool = True,
	) -> None:
		"""以人类方式移动鼠标到目标位置。

		Args:
			x, y: 目标坐标。
			from_current: True=从当前位置开始，False=从随机位置开始。
		"""
		await self._ensure_viewport()

		if from_current:
			try:
				# Playwright 没有直接获取鼠标位置的 API，
				# 用 viewport 内部估算
				start_x = random.randint(100, self._viewport_w - 100)  # type: ignore[operator]
				start_y = random.randint(100, self._viewport_h - 100)  # type: ignore[operator]
			except Exception:
				start_x, start_y = 500, 400
		else:
			start_x = random.randint(100, self._viewport_w - 100)  # type: ignore[operator]
			start_y = random.randint(100, self._viewport_h - 100)  # type: ignore[operator]

		path = generate_mouse_path(
			(start_x, start_y),
			(x, y),
			speed=self._profile.random_mouse_speed(),
		)

		for px, py in path:
			try:
				await self._page.mouse.move(px, py)
			except Exception:
				pass
			# 每个微步之间的延迟（路径点越密越快）
			step_delay = 0.005 / self._profile.random_mouse_speed()
			await asyncio.sleep(step_delay)

	async def click(
		self,
		selector: str,
		*,
		hover_first: bool = True,
		**kwargs: Any,
	) -> None:
		"""模拟人类点击。

		1. 先 hover（真人会先看再点）
		2. 停顿一小会
		3. 点击
		4. 点击后停顿
		"""
		await self._natural_gap()
		locator = self._page.locator(selector).first
		box = await locator.bounding_box()
		if box is None:
			# 找不到元素，直接点击
			await locator.click(**kwargs)
			return

		# 在元素内部随机偏移（不点正中心）
		offset_x = random.randint(-int(box["width"] * 0.2), int(box["width"] * 0.2))
		offset_y = random.randint(-int(box["height"] * 0.2), int(box["height"] * 0.2))
		target_x = int(box["x"] + box["width"] / 2 + offset_x)
		target_y = int(box["y"] + box["height"] / 2 + offset_y)

		# 移动鼠标到目标
		if hover_first:
			await self.move_mouse_to(target_x, target_y)
			await asyncio.sleep(self._profile.random_click_delay())

		# 点击
		await self._page.mouse.click(target_x, target_y)
		await asyncio.sleep(self._profile.random_click_delay() * 0.3)

	async def type(
		self,
		selector: str,
		text: str,
		*,
		click_first: bool = True,
	) -> None:
		"""模拟人类打字。

		1. 点击输入框
		2. 逐字符输入，每个字符间隔随机
		3. 偶尔打错 → 退格 → 重打
		"""
		await self._natural_gap()

		if click_first:
			await self.click(selector, hover_first=True)
			await asyncio.sleep(0.2)

		locator = self._page.locator(selector).first

		sequence = generate_typing_sequence(text, self._profile)
		for ch, delay in sequence:
			if ch == "\b":
				await locator.press("Backspace")
			elif ch == "\n":
				await locator.press("Enter")
			else:
				await locator.press(ch)
			await asyncio.sleep(delay)

	async def scroll(
		self,
		distance: int,
		*,
		selector: str | None = None,
	) -> None:
		"""模拟人类滚动。

		不是一口气滚完，而是分多段，中间有阅读停顿。
		偶尔还会回滚一小段（看漏了往回找）。
		"""
		await self._natural_gap()

		target = self._page.locator(selector).first if selector else self._page
		segments = generate_scroll_sequence(abs(distance), self._profile)
		direction = -1 if distance < 0 else 1

		for step_px, pause_s in segments:
			actual_step = step_px * direction
			try:
				await target.evaluate(
					f"el => el.scrollBy({{top: {actual_step}, behavior: 'smooth'}})"
				)
			except Exception:
				# Fallback to wheel
				await self._page.mouse.wheel(0, actual_step)
			await asyncio.sleep(pause_s)

	async def reading_pause(self, *, min_seconds: float = 2.0, max_seconds: float = 8.0) -> None:
		"""模拟阅读停顿 — 真人在看页面内容。"""
		duration = random.uniform(min_seconds, max_seconds)

		# 偶尔在停顿中微滚一下（调整阅读位置）
		if random.random() < 0.3:
			await asyncio.sleep(duration * 0.6)
			await self._page.mouse.wheel(0, random.randint(-80, 80))
			await asyncio.sleep(duration * 0.4)
		else:
			await asyncio.sleep(duration)

		self._last_action_at = time.monotonic()

	async def hover_then_decide(
		self,
		selector: str,
		*,
		hover_seconds: float | None = None,
	) -> None:
		"""悬停在元素上一段时间再决定（模拟 HR 看候选人卡片）。"""
		await self._natural_gap()

		locator = self._page.locator(selector).first
		box = await locator.bounding_box()
		if box is None:
			return

		x = int(box["x"] + box["width"] / 2)
		y = int(box["y"] + box["height"] / 2)
		await self.move_mouse_to(x, y)
		await asyncio.sleep(hover_seconds or self._profile.random_scroll_pause())

	async def human_like_fill_form(
		self,
		fields: dict[str, str],
	) -> None:
		"""以人类方式填表 — 逐个字段填写，字段间有停顿。"""
		for selector, text in fields.items():
			await self.click(selector, hover_first=True)
			await asyncio.sleep(self._profile.random_click_delay())
			await self.type(selector, text, click_first=False)
			# 字段间的停顿（思考时间）
			await self.reading_pause(min_seconds=0.5, max_seconds=2.0)
