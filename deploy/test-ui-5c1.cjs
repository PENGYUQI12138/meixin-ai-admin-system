// Edge/Playwright clicks only against the disposable M3 isolation site.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.MEIXIN_PLAYWRIGHT_PATH || 'playwright');

const base = 'http://127.0.0.1:18081';
const fixtures = JSON.parse(process.env.MEIXIN_5C1_FIXTURES || '{}');
if (!process.env.MEIXIN_TEST_SECRET_DIR || !fixtures.reversal || !fixtures.refund || !fixtures.zero) {
  throw new Error('5C-1 isolated fixtures and secret directory are required');
}
const password = fs.readFileSync(path.join(process.env.MEIXIN_TEST_SECRET_DIR, 'admin-password'), 'utf8').trim();

async function login(browser, user) {
  const page = await browser.newPage({ locale: 'zh-CN' });
  await page.goto(`${base}/login`, { waitUntil: 'domcontentloaded' });
  assert.equal(new URL(page.url()).origin, base);
  await page.locator('#login_email').fill(user);
  await page.locator('#login_password').fill(password);
  await page.locator('.btn-login').first().click();
  await page.waitForURL(url => url.href.startsWith(base) && !url.pathname.endsWith('/login'));
  return page;
}

async function openPackage(page, name) {
  await page.goto(`${base}/desk/mx-student-package/${name}`, { waitUntil: 'domcontentloaded' });
  await page.locator('.mx-reverse-payment, .page-actions button').first().waitFor();
  await page.waitForTimeout(500);
  return await page.locator('body').innerText();
}

async function main() {
  const browser = await chromium.launch({ headless: true,
    executablePath: 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe' });
  const errors = [];
  try {
    const manager = await login(browser, 'm3-5b-manager@example.invalid');
    manager.on('pageerror', error => errors.push(error.message));
    manager.on('console', message => {
      if (message.type() === 'error' || message.type() === 'warning') errors.push(message.text());
    });
    let body = await openPackage(manager, fixtures.reversal.package);
    assert.match(body, /派生状态：生效.*已付净额：¥1\.23.*剩余课时：20/s);
    assert.equal(await manager.locator('.mx-reverse-payment').count(), 1);
    await manager.locator('.mx-reverse-payment').click();
    await manager.waitForTimeout(350);
    assert.match(await manager.locator('body').innerText(), /原记录会保留并新增反向流水/);
    await manager.locator('.modal:visible textarea[data-fieldname="reason"]').fill('5C-1 隔离撤销验收');
    await manager.getByRole('button', { name: '确认撤销收款' }).click();
    await manager.waitForTimeout(650);
    body = await manager.locator('body').innerText();
    assert.match(body, /派生状态：欠费冻结.*已付净额：¥0\.00.*剩余课时：20/s);
    assert.match(body, /5C-1 隔离撤销验收/);
    assert.equal(await manager.locator('.mx-reverse-payment').count(), 0);
    body = await openPackage(manager, fixtures.refund.package);
    assert.match(body, /派生状态：生效.*已付净额：¥1\.23.*剩余课时：20/s);
    await manager.getByRole('button', { name: '退款关闭课包' }).click();
    await manager.waitForTimeout(350);
    assert.match(await manager.locator('body').innerText(), /退款关闭整包，不是部分退款后继续使用/);
    await manager.locator('.modal:visible input[data-fieldname="amount"]').fill('1.24');
    await manager.locator('.modal:visible select[data-fieldname="payment_method"]').selectOption('银行转账');
    await manager.locator('.modal:visible textarea[data-fieldname="reason"]').fill('5C-1 隔离整包退款');
    await manager.getByRole('button', { name: '确认退款并关闭课包' }).click();
    await manager.waitForTimeout(550);
    assert.match(await manager.locator('body').innerText(), /退款金额必须大于 0 且不得超过当前有效净收款/);
    const expectedRejection = errors.splice(0);
    assert(expectedRejection.some(message => message.includes('退款金额必须大于 0')));
    await manager.locator('.modal:visible .btn-modal-close').last().click();
    await manager.locator('.modal:visible input[data-fieldname="amount"]').fill('1.00');
    await manager.getByRole('button', { name: '确认退款并关闭课包' }).click();
    await manager.waitForTimeout(700);
    body = await manager.locator('body').innerText();
    assert.match(body, /派生状态：已退款关闭.*已付净额：¥0\.23.*剩余课时：0/s);
    assert.match(body, /退款收回\s+-20/);
    assert.match(body, /5C-1 隔离整包退款/);
    assert.equal(await manager.getByRole('button', { name: '退款关闭课包' }).count(), 0);
    const scheduler = await login(browser, 'm3-5b-scheduler@example.invalid');
    await openPackage(scheduler, fixtures.zero.package);
    assert.equal(await scheduler.getByRole('button', { name: '退款关闭课包' }).count(), 0);
    assert.equal(await scheduler.locator('.mx-reverse-payment').count(), 0);
    body = await openPackage(manager, fixtures.zero.package);
    assert.match(body, /派生状态：已耗尽.*剩余课时：0/s);
    await manager.getByRole('button', { name: '退款关闭课包' }).click();
    await manager.waitForTimeout(300);
    assert.match(await manager.locator('body').innerText(), /当前剩余课时：0/);
    await manager.locator('.modal:visible input[data-fieldname="amount"]').fill('1.23');
    await manager.locator('.modal:visible select[data-fieldname="payment_method"]').selectOption('现金');
    await manager.locator('.modal:visible textarea[data-fieldname="reason"]').fill('5C-1 零余额关闭');
    let zeroRefundRequests = 0;
    manager.on('request', request => {
      if (request.url().includes('meixin_admin.payments.refund_close_package')) zeroRefundRequests++;
    });
    await manager.getByRole('button', { name: '确认退款并关闭课包' }).dblclick();
    await manager.waitForTimeout(700);
    body = await manager.locator('body').innerText();
    assert.match(body, /派生状态：已退款关闭.*剩余课时：0/s);
    assert.doesNotMatch(body, /退款收回/);
    assert.equal(zeroRefundRequests, 1);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ phase: 'accepted', reversal: fixtures.reversal.package,
      refund: fixtures.refund.package, zeroBalanceRefund: fixtures.zero.package,
      expectedOverLimitConsoleEvents: expectedRejection.length, zeroRefundRequests, consoleErrors: errors }));
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
