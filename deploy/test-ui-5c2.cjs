// Edge/Playwright clicks only against the disposable M3 isolation site.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.MEIXIN_PLAYWRIGHT_PATH || 'playwright');

const base = 'http://127.0.0.1:18081';
const fixture = JSON.parse(process.env.MEIXIN_5C2_FIXTURE || '{}');
if (!process.env.MEIXIN_TEST_SECRET_DIR || !fixture.package || !fixture.refund || !fixture.student || !fixture.plan) {
  throw new Error('5C-2 isolated fixture and secret directory are required');
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
  await page.waitForTimeout(650);
  return page.locator('body').innerText();
}

async function main() {
  const browser = await chromium.launch({ headless: true,
    executablePath: 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe' });
  const errors = [];
  try {
    const scheduler = await login(browser, 'm3-5b-scheduler@example.invalid');
    await scheduler.goto(`${base}/desk/mx-student-package`, { waitUntil: 'domcontentloaded' });
    await scheduler.waitForTimeout(650);
    assert.equal(await scheduler.getByRole('button', { name: '赠送课包' }).count(), 0);
    await openPackage(scheduler, fixture.package);
    assert.equal(await scheduler.getByRole('button', { name: '人工调整课时' }).count(), 0);
    assert.equal(await scheduler.locator('.mx-reverse-payment').count(), 0);

    const manager = await login(browser, 'm3-5b-manager@example.invalid');
    manager.on('pageerror', error => errors.push(error.message));
    manager.on('console', message => {
      if (message.type() === 'error' || message.type() === 'warning') errors.push(message.text());
    });
    let reversalRequests = 0;
    manager.on('request', request => {
      if (request.url().includes('meixin_admin.payments.reverse_payment')) reversalRequests++;
    });
    const closed = await openPackage(manager, fixture.package);
    if (closed.includes('派生状态：已退款关闭')) {
      assert.match(closed, /派生状态：已退款关闭.*已付净额：¥0\.23.*剩余课时：0/s);
      assert.equal(await manager.getByRole('button', { name: '人工调整课时' }).count(), 0);
      assert.equal(await manager.locator('.mx-reverse-payment').count(), 1);
      await manager.locator('.mx-reverse-payment').click();
      await manager.waitForTimeout(350);
      assert.match(await manager.locator('body').innerText(), /原退款金额 ¥1\.00，原收回课时 19/);
      await manager.locator('.modal:visible textarea[data-fieldname="reason"]').fill('5C-2 隔离退款撤销');
      await manager.getByRole('button', { name: '确认撤销退款关闭' }).dblclick();
      await manager.waitForTimeout(800);
      assert.equal(reversalRequests, 1);
    }
    let body = await manager.locator('body').innerText();
    assert.match(body, /派生状态：生效.*已付净额：¥1\.23.*剩余课时：19/s);
    assert.match(body, /退款 reversal 恢复\s+19/);
    assert.match(body, /人工调整\s+-1/);
    assert.equal(await manager.getByRole('button', { name: '撤销退款关闭' }).count(), 0);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ phase: 'refund-reversal-accepted', package: fixture.package, reversalRequests }));

    await manager.goto(`${base}/desk/mx-student-package`, { waitUntil: 'domcontentloaded' });
    await manager.getByRole('button', { name: '赠送课包' }).waitFor();
    await manager.getByRole('button', { name: '赠送课包' }).click();
    await manager.waitForTimeout(650);
    assert.match(manager.url(), /new-mx-student-package/);
    assert.match(await manager.locator('body').innerText(), /获取类型\s+赠送/);
    assert.equal(await manager.getByRole('button', { name: '创建赠送草稿' }).count(), 1);
    await manager.locator('input[data-fieldname="student"]:visible').fill(fixture.student);
    await manager.getByRole('option', { name: /5B虚构学生/ }).click();
    await manager.locator('input[data-fieldname="package_plan"]:visible').fill(fixture.plan);
    await manager.waitForTimeout(350);
    await manager.getByRole('option', { name: /5B虚构课包/ }).click();
    await manager.waitForTimeout(350);
    body = await manager.locator('body').innerText();
    assert.match(body, /赠送成交金额为 ¥0\.00，不产生收款/);
    await manager.getByRole('button', { name: '创建赠送草稿' }).click();
    await manager.waitForTimeout(650);
    const giftPackage = manager.url().split('/').at(-1);
    assert.notEqual(giftPackage, fixture.package);
    await manager.getByRole('button', { name: '提交', exact: true }).click();
    await manager.getByRole('button', { name: '是', exact: true }).click();
    await manager.waitForTimeout(850);
    body = await manager.locator('body').innerText();
    assert.match(body, /获取类型\s+赠送/);
    assert.match(body, /派生状态：生效.*应收：¥0\.00.*已付净额：¥0\.00.*剩余课时：20/s);
    assert.match(body, /赠送授予\s+20/);
    assert.match(body, /暂无付款记录/);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ phase: 'gift-accepted', giftPackage }));

    await manager.getByRole('button', { name: '人工调整课时' }).click();
    await manager.waitForTimeout(250);
    assert.match(await manager.locator('body').innerText(), /当前派生余额：20 课时/);
    await manager.locator('.modal:visible select[data-fieldname="direction"]').selectOption('增加');
    await manager.locator('.modal:visible input[data-fieldname="quantity"]').fill('3');
    await manager.locator('.modal:visible textarea[data-fieldname="reason"]').fill('5C-2 隔离正调整');
    let adjustmentRequests = 0;
    manager.on('request', request => {
      if (request.url().includes('meixin_admin.entitlements.adjust_credits')) adjustmentRequests++;
    });
    await manager.getByRole('button', { name: '确认人工调整' }).dblclick();
    await manager.waitForTimeout(650);
    body = await manager.locator('body').innerText();
    assert.match(body, /剩余课时：23/);
    assert.match(body, /人工调整\s+3.*5C-2 隔离正调整/s);
    assert.equal(adjustmentRequests, 1);

    await manager.getByRole('button', { name: '人工调整课时' }).click();
    await manager.locator('.modal:visible select[data-fieldname="direction"]').selectOption('减少');
    await manager.locator('.modal:visible input[data-fieldname="quantity"]').fill('4');
    await manager.locator('.modal:visible textarea[data-fieldname="reason"]').fill('5C-2 隔离负调整');
    await manager.getByRole('button', { name: '确认人工调整' }).click();
    await manager.waitForTimeout(650);
    body = await manager.locator('body').innerText();
    assert.match(body, /剩余课时：19/);
    assert.match(body, /人工调整\s+-4.*5C-2 隔离负调整/s);

    await manager.getByRole('button', { name: '人工调整课时' }).click();
    await manager.locator('.modal:visible select[data-fieldname="direction"]').selectOption('减少');
    await manager.locator('.modal:visible input[data-fieldname="quantity"]').fill('20');
    await manager.locator('.modal:visible textarea[data-fieldname="reason"]').fill('5C-2 隔离拒绝负余额后重试');
    await manager.getByRole('button', { name: '确认人工调整' }).click();
    await manager.waitForTimeout(500);
    assert.match(await manager.locator('body').innerText(), /余额不足|负余额/);
    const expectedRejection = errors.splice(0);
    assert(expectedRejection.length > 0);
    await manager.locator('.modal:visible .btn-modal-close').last().click();
    await manager.locator('.modal:visible input[data-fieldname="quantity"]').fill('1');
    await manager.getByRole('button', { name: '确认人工调整' }).click();
    await manager.waitForTimeout(650);
    body = await manager.locator('body').innerText();
    assert.match(body, /剩余课时：18/);
    assert.match(body, /人工调整\s+-1.*5C-2 隔离拒绝负余额后重试/s);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ phase: 'accepted', refundReversalPackage: fixture.package, giftPackage,
      reversalRequests, adjustmentRequests, expectedOverdrawConsoleEvents: expectedRejection.length,
      consoleErrors: errors }));
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
