import test from 'node:test';
import assert from 'node:assert/strict';

import { selectBossTab } from './tab_selection.js';

test('优先当前激活的 BOSS 标签', () => {
	const selected = selectBossTab([
		{ id: 1, url: 'https://www.zhipin.com/web/chat/index', lastAccessed: 200 },
		{ id: 2, url: 'https://www.zhipin.com/web/chat/index', lastAccessed: 100 },
	], 2);

	assert.equal(selected?.id, 2);
});

test('当前窗口不是 BOSS 时优先最近使用的沟通页', () => {
	const selected = selectBossTab([
		{ id: 1, url: 'https://www.zhipin.com/web/chat/index', lastAccessed: 100 },
		{ id: 2, url: 'https://www.zhipin.com/web/geek/recommend', lastAccessed: 300 },
		{ id: 3, url: 'https://www.zhipin.com/web/chat/index', lastAccessed: 200 },
	], null);

	assert.equal(selected?.id, 3);
});
