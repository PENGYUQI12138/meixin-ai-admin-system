// Run only against the disposable M3 isolation site with preinstalled Playwright + Edge.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.MEIXIN_PLAYWRIGHT_PATH || 'playwright');

const base = 'http://127.0.0.1:18081';
const secretDir = process.env.MEIXIN_TEST_SECRET_DIR;
if (!secretDir || !process.env.MEIXIN_5B_STUDENT || !process.env.MEIXIN_5B_PLAN) {
  throw new Error('5B isolated browser fixture and secret directory are required');
}
const password = fs.readFileSync(path.join(secretDir, 'admin-password'), 'utf8').trim();

async function login(browser, user) {
  const page = await browser.newPage({ locale: 'zh-CN' });
  await page.goto(`${base}/login`, { waitUntil: 'domcontentloaded' });
  if (!page.url().startsWith(base)) throw new Error(`Unexpected browser origin: ${page.url()}`);
  await page.locator('#login_email').fill(user);
  await page.locator('#login_password').fill(password);
  await page.locator('.btn-login').first().click();
  await page.waitForURL(url => url.href.startsWith(base) && !url.pathname.endsWith('/login'), { timeout: 20000 });
  return page;
}

async function main() {
  const browser = await chromium.launch({
    headless: true,
    executablePath: 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  });
  const errors = [];
  try {
    const page = await login(browser, 'm3-5b-scheduler@example.invalid');
    page.on('console', message => {
      if (message.type() === 'error' || message.type() === 'warning') errors.push(`${message.type()}: ${message.text()}`);
    });
    page.on('pageerror', error => errors.push(`pageerror: ${error.message}`));
    await page.goto(`${base}/desk/query-report/学生课包概览`, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(1200);
    const button = page.getByRole('button', { name: '购买/续费课包' });
    if (!(await button.count())) throw new Error('Purchase button missing from report');
    await button.click();
    await page.waitForTimeout(1000);
    await page.locator('input[data-fieldname="student"]:visible').fill(process.env.MEIXIN_5B_STUDENT);
    await page.getByText('5B虚构学生', { exact: true }).click();
    await page.locator('input[data-fieldname="package_plan"]:visible').fill(process.env.MEIXIN_5B_PLAN);
    await page.waitForTimeout(500);
    await page.getByText('5B虚构课包', { exact: true }).click();
    await page.waitForTimeout(400);
    assert.match(await page.locator('body').innerText(), /标准价格：CNY 1\.23/);
    await page.getByRole('button', { name: '创建购买草稿' }).click();
    await page.waitForTimeout(1000);
    await page.getByRole('button', { name: '提交', exact: true }).click();
    await page.waitForTimeout(350);
    await page.getByRole('button', { name: '是', exact: true }).click();
    await page.waitForTimeout(950);
    const firstPackage = page.url().split('/').at(-1);
    assert.match(await page.locator('body').innerText(), /派生状态：待付款.*尚需付款：¥1\.23/s);
    await page.getByRole('button', { name: '录入收款' }).click();
    await page.waitForTimeout(400);
    await page.locator('.modal:visible input[data-fieldname="amount"]').fill('0.10');
    await page.locator('.modal:visible select[data-fieldname="payment_method"]').selectOption('现金');
    await page.locator('.modal:visible textarea[data-fieldname="note"]').fill('5B隔离浏览器首付款');
    await page.getByRole('button', { name: '确认收款' }).click();
    await page.waitForTimeout(850);
    assert.match(await page.locator('body').innerText(), /已付净额：¥0\.10.*尚需付款：¥1\.13/s);
    assert.match(await page.locator('body').innerText(), /暂无权益流水/);
    await page.getByRole('button', { name: '录入收款' }).click();
    await page.locator('.modal:visible input[data-fieldname="amount"]').fill('1.13');
    await page.locator('.modal:visible select[data-fieldname="payment_method"]').selectOption('银行转账');
    await page.getByRole('button', { name: '确认收款' }).click();
    await page.waitForTimeout(900);
    const paidBody = await page.locator('body').innerText();
    assert.match(paidBody, /派生状态：生效.*已付净额：¥1\.23.*尚需付款：¥0\.00.*剩余课时：20/s);
    assert.match(paidBody, /购买授予\s+20/);
    assert.equal((paidBody.match(/\t收款\t/g) || []).length, 2);
    assert.equal(await page.getByRole('button', { name: '录入收款' }).count(), 0);
    await page.getByRole('button', { name: '续费/新购课包' }).click();
    await page.waitForTimeout(500);
    assert.match(page.url(), /new-mx-student-package/);
    assert.equal(await page.locator('input[data-fieldname="student"]:visible').inputValue(), '5B虚构学生');
    assert.equal(await page.locator('input[data-fieldname="package_plan"]:visible').inputValue(), '5B虚构课包');
    await page.getByRole('button', { name: '创建购买草稿' }).click();
    await page.waitForTimeout(500);
    const renewalPackage = page.url().split('/').at(-1);
    assert.notEqual(renewalPackage, firstPackage);
    await page.getByRole('button', { name: '提交', exact: true }).click();
    await page.getByRole('button', { name: '是', exact: true }).click();
    await page.waitForTimeout(550);
    assert.match(await page.locator('body').innerText(), /派生状态：待付款/);
    const managerPage = await login(browser, 'm3-5b-manager@example.invalid');
    managerPage.on('console', message => {
      if (message.type() === 'error' || message.type() === 'warning') errors.push(`${message.type()}: ${message.text()}`);
    });
    managerPage.on('pageerror', error => errors.push(`pageerror: ${error.message}`));
    await managerPage.goto(`${base}/desk/mx-student-package/${renewalPackage}`, { waitUntil: 'domcontentloaded' });
    await managerPage.getByRole('button', { name: '录入收款' }).waitFor();
    await managerPage.getByRole('button', { name: '录入收款' }).click();
    await managerPage.locator('.modal:visible input[data-fieldname="amount"]').fill('1.23');
    await managerPage.locator('.modal:visible select[data-fieldname="payment_method"]').selectOption('现金');
    await managerPage.getByRole('button', { name: '确认收款' }).click();
    await managerPage.waitForTimeout(550);
    assert.match(await managerPage.locator('body').innerText(), /派生状态：生效.*剩余课时：20/s);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ phase: 'accepted', firstPackage, renewalPackage,
      scheduler: 'partial/full payment and one grant', manager: 'renewal full payment and one grant', consoleErrors: errors }));
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
