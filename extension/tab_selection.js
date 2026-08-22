/**
 * 为 Bridge 选择一个可复用的 BOSS 标签页。
 *
 * 扩展不能把 chrome.tabs.query 返回的第一项当作用户当前工作页：同一账号可能
 * 同时打开职位、推荐和沟通页面，返回顺序不代表用户正在使用哪个标签。本模块
 * 保持纯函数，既便于回归测试，也避免标签选择规则散落在命令处理逻辑中。
 */

/**
 * 选择最符合用户当前工作上下文的 BOSS 页面。
 *
 * 规则按风险从低到高排序：已激活的 BOSS 标签最能代表用户意图；若当前焦点
 * 不在 BOSS，则选择最近使用的沟通页；最后才回退到最近使用的其他 BOSS 页。
 * 不返回的标签页由调用方自行创建自动化窗口，避免本函数隐式改变浏览器状态。
 *
 * @param {Array<chrome.tabs.Tab>} tabs 允许附着的 BOSS 标签集合。
 * @param {number | null} activeBossTabId 当前焦点窗口里的 BOSS 标签标识。
 * @returns {chrome.tabs.Tab | null} 最适合复用的标签，或空值。
 */
export function selectBossTab(tabs, activeBossTabId) {
	const validTabs = tabs.filter((tab) => Number.isInteger(tab?.id));
	if (validTabs.length === 0) return null;

	const activeTab = validTabs.find((tab) => tab.id === activeBossTabId);
	if (activeTab) return activeTab;

	const recentFirst = (left, right) => (right.lastAccessed || 0) - (left.lastAccessed || 0)
		|| (left.id || 0) - (right.id || 0);
	const chatTabs = validTabs
		.filter((tab) => /\/web\/chat\//.test(tab.url || ''))
		.sort(recentFirst);
	return chatTabs[0] || validTabs.sort(recentFirst)[0];
}
