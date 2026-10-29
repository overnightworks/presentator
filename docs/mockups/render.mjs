// Renders the lobby boards of m0-lobby.html: one PNG per board and theme, cropped to the board.
// Run with Playwright resolvable (NODE_PATH), from anywhere:
//   node docs/mockups/render.mjs [board-id ...]   without ids: every lobby board and the overview
import { chromium } from 'playwright';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const mockups = path.dirname(fileURLToPath(import.meta.url));
const only = new Set(process.argv.slice(2));
const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1800, height: 1200 }, deviceScaleFactor: 2, reducedMotion: 'reduce' });
const page = await context.newPage();
await page.goto(`file://${path.join(mockups, 'm0-lobby.html')}`);
await page.evaluate(() => document.fonts.ready);

const ids = await page.$$eval('figure.board[data-addr]', (figures) => figures.map((figure) => figure.id));
for (const theme of ['light', 'dark']) {
	await page.click(`[data-set-theme="${theme}"]`);
	for (const id of ids.filter((boardId) => only.size === 0 || only.has(boardId))) {
		const file = `${id}${theme === 'dark' ? '-dark' : ''}.png`;
		await page.locator(`#${id}`).screenshot({ path: path.join(mockups, 'm0-lobby', file) });
	}
}
if (only.size === 0) {
	await page.click('[data-set-theme="light"]');
	const overview = await browser.newContext({ viewport: { width: 1800, height: 1200 }, deviceScaleFactor: 0.5, reducedMotion: 'reduce' });
	const overviewPage = await overview.newPage();
	await overviewPage.goto(`file://${path.join(mockups, 'm0-lobby.html')}`);
	await overviewPage.evaluate(() => document.fonts.ready);
	await overviewPage.click('[data-set-theme="light"]');
	await overviewPage.screenshot({ path: path.join(mockups, 'm0-lobby-sheet.png'), fullPage: true });
}
await browser.close();
