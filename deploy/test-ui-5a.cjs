// Isolated browser smoke check; uses the already bundled Playwright and Edge.
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.MEIXIN_PLAYWRIGHT_PATH || 'playwright');

async function main() {
const base = 'http://127.0.0.1:18081';
const secretDir = process.env.MEIXIN_TEST_SECRET_DIR;
if (!secretDir) throw new Error('MEIXIN_TEST_SECRET_DIR is required');
const password = fs.readFileSync(path.join(secretDir, 'admin-password'), 'utf8').trim();
const browser = await chromium.launch({
  headless: true,
  executablePath: 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
});
const page = await browser.newPage({ locale: 'zh-CN' });
async function login(tab, user) {
  await tab.goto(`${base}/login`, { waitUntil: 'domcontentloaded' });
  if (!tab.url().startsWith(base)) throw new Error(`Unexpected browser target: ${tab.url()}`);
  await tab.locator('#login_email').fill(user);
  await tab.locator('#login_password').fill(password);
  await tab.locator('.btn-login').first().click();
  await tab.waitForURL(url => url.href.startsWith(base) && !url.pathname.endsWith('/login'), { timeout: 20000 });
}
const errors = [];
page.on('console', message => {
  if (message.type() === 'error' || message.type() === 'warning') errors.push(`${message.type()}: ${message.text()}`);
});
page.on('pageerror', error => errors.push(`pageerror: ${error.message}`));
const results = {};
try {
  await login(page, 'Administrator');
  if (process.env.MEIXIN_5A_TITLES_ONLY === '1') {
    for (const route of ['mx-package-plan', 'mx-payment', 'mx-lesson-credit-entry']) {
      await page.goto(`${base}/desk/${route}`, { waitUntil: 'domcontentloaded' });
      await page.waitForTimeout(900);
      results[route] = (await page.locator('body').innerText()).slice(0, 350);
    }
    process.stdout.write(JSON.stringify(results, null, 2));
    return;
  }
  const routes = [
    ['workspace', '/desk/美心行政'],
    ['plan', '/desk/mx-package-plan'],
    ['packages', '/desk/query-report/学生课包概览'],
    ['payment', '/desk/mx-payment'],
    ['credit', '/desk/mx-lesson-credit-entry'],
    ['plan_detail', `/desk/mx-package-plan/${process.env.MEIXIN_5A_PLAN}`],
    ['package_detail', `/desk/mx-student-package/${process.env.MEIXIN_5A_PACKAGE}`],
    ['payment_detail', `/desk/mx-payment/${process.env.MEIXIN_5A_PAYMENT}`],
    ['credit_detail', `/desk/mx-lesson-credit-entry/${process.env.MEIXIN_5A_CREDIT}`],
  ];
  for (const [name, route] of routes) {
    await page.goto(base + route, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(1800);
    results[name] = {
      url: page.url(), title: await page.title(),
      text: (await page.locator('body').innerText()).slice(0, 2500),
    };
    if (name === 'workspace') {
      results.navigation = await page.locator('a').evaluateAll(links => links
        .filter(link => ['课包产品', '学生已购课包', '收款记录', '课时权益流水'].includes(link.textContent.trim()))
        .map(link => ({ label: link.textContent.trim(), href: link.getAttribute('href') })));
      results.clickedNavigation = {};
      for (const link of results.navigation) {
        await page.locator(`a[href="${link.href}"]`).first().click();
        await page.waitForURL(url => decodeURI(url.pathname) === decodeURI(link.href), { timeout: 10000 });
        results.clickedNavigation[link.label] = decodeURI(new URL(page.url()).pathname) === decodeURI(link.href);
        await page.goto(`${base}/desk/美心行政`, { waitUntil: 'domcontentloaded' });
      }
    }
    if (name === 'workspace' || name === 'package_detail') {
      await page.screenshot({ path: path.join(secretDir, `m3-5a-${name}.png`), fullPage: true });
    }
  }
  for (const user of ['m3-5a-scheduler@example.invalid', 'm3-5a-restricted@example.invalid']) {
    const tab = await browser.newPage({ locale: 'zh-CN' });
    tab.on('console', message => {
      if (message.type() === 'error' || message.type() === 'warning') errors.push(`${user}: ${message.type()}: ${message.text()}`);
    });
    tab.on('pageerror', error => errors.push(`${user}: pageerror: ${error.message}`));
    await login(tab, user);
    await tab.goto(`${base}/desk/query-report/学生课包概览`, { waitUntil: 'domcontentloaded' });
    await tab.waitForTimeout(1800);
    const reportText = await tab.locator('body').innerText();
    await tab.goto(`${base}/desk/mx-student-package/${process.env.MEIXIN_5A_PACKAGE}`, { waitUntil: 'domcontentloaded' });
    await tab.waitForTimeout(1800);
    const detailText = await tab.locator('body').innerText();
    results[user] = {
      reportHasPackage: reportText.includes(process.env.MEIXIN_5A_PACKAGE),
      detailHasCredits: detailText.includes('剩余课时：20'),
      detailHasCancel: detailText.includes('\n取消\n'),
      detailTail: detailText.slice(0, 600),
    };
    await tab.close();
  }
  const summary = {
    navigation: results.navigation,
    clickedNavigation: results.clickedNavigation,
    listRows: Object.fromEntries(['plan', 'packages', 'payment', 'credit'].map(name =>
      [name, results[name].text.includes(name === 'packages' ? process.env.MEIXIN_5A_PACKAGE :
        name === 'plan' ? process.env.MEIXIN_5A_PLAN :
        name === 'payment' ? process.env.MEIXIN_5A_PAYMENT : process.env.MEIXIN_5A_CREDIT)])),
    packageSummary: results.package_detail.text.includes('派生状态：生效') &&
      results.package_detail.text.includes('已付净额：¥1.23') &&
      results.package_detail.text.includes('剩余课时：20'),
    sourceDetails: results.credit_detail.text.includes('MX Student Package') &&
      results.credit_detail.text.includes('5A虚构课包') &&
      results.payment_detail.text.includes('CNY 1.23'),
    scheduler: results['m3-5a-scheduler@example.invalid'],
    restricted: results['m3-5a-restricted@example.invalid'],
    consoleCount: errors.length,
    consoleKinds: errors.map(error => error.startsWith('warning:') ? 'Frappe preload warning' :
      error.includes('403') || error.includes('PermissionError') ? 'expected permission denial' : error.slice(0, 100)),
  };
  process.stdout.write(JSON.stringify(summary, null, 2));
  if (summary.navigation.length !== 4 ||
      Object.values(summary.clickedNavigation).some(value => !value) ||
      Object.values(summary.listRows).some(value => !value) ||
      !summary.packageSummary || !summary.sourceDetails ||
      !summary.scheduler.reportHasPackage || !summary.scheduler.detailHasCredits ||
      summary.scheduler.detailHasCancel || summary.restricted.reportHasPackage ||
      summary.restricted.detailHasCredits) process.exitCode = 1;
} finally {
  await browser.close();
}
}

main().catch(error => { console.error(error); process.exitCode = 1; });
