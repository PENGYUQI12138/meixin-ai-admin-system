// End-to-end Desk acceptance; fixed disposable isolation origin only.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.MEIXIN_PLAYWRIGHT_PATH || 'playwright');

const base = 'http://127.0.0.1:18081';
if (!process.env.MEIXIN_TEST_SECRET_DIR) throw new Error('Isolation secret directory required');
const password = fs.readFileSync(path.join(process.env.MEIXIN_TEST_SECRET_DIR, 'admin-password'), 'utf8').trim();
const fixture = JSON.parse(process.env.MEIXIN_5D_FIXTURE || '{}');
const unexpectedErrors = errors => errors.filter(error => !error.includes('was preloaded using link preload'));

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

async function newDoc(page, slug) {
  await page.goto(`${base}/desk/${slug}/new-${slug}`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(450);
  assert.equal(new URL(page.url()).origin, base);
}

async function field(page, name, value) {
  await page.locator(`input[data-fieldname="${name}"]:visible`).fill(String(value));
}

async function link(page, name, query, title) {
  await field(page, name, query);
  await page.getByRole('option', { name: new RegExp(title) }).click();
}

async function save(page) {
  await page.getByRole('button', { name: '保存', exact: true }).click();
  await page.waitForTimeout(550);
  const name = page.url().split('/').at(-1);
  assert(!name.startsWith('new-'), `Document not saved: ${page.url()}`);
  return name;
}

async function directApi(page, method, args) {
  return page.evaluate(async ({ method, args }) => {
    try {
      const result = await frappe.call({ method, args });
      return { ok: true, message: result.message };
    } catch (error) {
      return { ok: false, error: String(error?.message || error) };
    }
  }, { method, args });
}

async function consumeNewSession(page, fixture, start, earlyReason) {
  await newDoc(page, 'mx-session');
  await link(page, 'course', fixture.course, '5D虚构课程');
  await link(page, 'teacher', fixture.teacher, '5D虚构教师');
  await link(page, 'room', fixture.room, '5D虚构教室');
  await field(page, 'start_at', start);
  await page.locator('input[data-fieldname="start_at"]:visible').blur();
  await page.waitForTimeout(350);
  await page.locator('[data-fieldname="students"] .grid-row[data-name] [data-fieldname="student"]').click();
  await link(page, 'student', fixture.student, '5D虚构学生');
  const session = await save(page);
  await page.getByRole('button', { name: '提交', exact: true }).click();
  await page.getByRole('button', { name: '是', exact: true }).click();
  await page.waitForTimeout(500);
  await page.getByRole('button', { name: '记录上课结果' }).click();
  await page.waitForTimeout(400);
  await page.locator('[data-fieldname="attendance"] .grid-row[data-name] [data-fieldname="attendance_status"]').click();
  await page.locator('select[data-fieldname="attendance_status"]:visible').selectOption('到课');
  if (earlyReason) await page.locator('textarea[data-fieldname="early_completion_reason"]:visible').fill(earlyReason);
  const execution = await save(page);
  await page.getByRole('button', { name: '提交', exact: true }).click();
  await page.getByRole('button', { name: '是', exact: true }).click();
  await page.waitForTimeout(600);
  return { session, execution };
}

async function main() {
  const browser = await chromium.launch({ headless: true,
    executablePath: 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe' });
  const errors = [];
  try {
    const admin = await login(browser, 'Administrator');
    admin.on('pageerror', error => errors.push(`pageerror: ${error.message}`));
    admin.on('console', message => {
      if (['error', 'warning'].includes(message.type())) errors.push(`${message.type()}: ${message.text()}`);
    });
    if (!fixture.student) {
      await newDoc(admin, 'mx-student');
      await field(admin, 'student_name', '5D虚构学生');
      await field(admin, 'guardian_phone', '00000000000');
      fixture.student = await save(admin);
      await newDoc(admin, 'mx-student');
      await field(admin, 'student_name', '5D权限参照学生');
      await field(admin, 'guardian_phone', '00000000001');
      fixture.hiddenStudent = await save(admin);
      await newDoc(admin, 'mx-course');
      await field(admin, 'course_name', '5D虚构课程');
      await field(admin, 'default_duration_minutes', 60);
      fixture.course = await save(admin);
      await newDoc(admin, 'mx-teacher');
      await field(admin, 'teacher_name', '5D虚构教师');
      fixture.teacher = await save(admin);
      await newDoc(admin, 'mx-room');
      await field(admin, 'room_name', '5D虚构教室');
      await field(admin, 'capacity', 2);
      fixture.room = await save(admin);
      await newDoc(admin, 'mx-package-plan');
      await field(admin, 'plan_name', '5D虚构课包');
      await link(admin, 'course', fixture.course, '5D虚构课程');
      await field(admin, 'standard_credits', 20);
      await field(admin, 'standard_price', '1.23');
      fixture.plan = await save(admin);
      console.log(JSON.stringify({ phase: 'masters', ...fixture, errors }));
    }
    if (process.env.MEIXIN_5D_MASTERS_ONLY === '1') return;
    if (!fixture.package) {
      const scheduler = await login(browser, 'm3-5d-scheduler@example.invalid');
      scheduler.on('pageerror', error => errors.push(`scheduler pageerror: ${error.message}`));
      scheduler.on('console', message => {
        if (['error', 'warning'].includes(message.type())) errors.push(`scheduler ${message.type()}: ${message.text()}`);
      });
      await scheduler.goto(`${base}/desk/mx-student-package`, { waitUntil: 'domcontentloaded' });
      await scheduler.getByRole('button', { name: '购买/续费课包' }).click();
      await scheduler.waitForTimeout(500);
      await link(scheduler, 'student', fixture.student, '5D虚构学生');
      await link(scheduler, 'package_plan', fixture.plan, '5D虚构课包');
      await scheduler.getByRole('button', { name: '创建购买草稿' }).click();
      await scheduler.waitForTimeout(600);
      fixture.package = scheduler.url().split('/').at(-1);
      assert.match(fixture.package, /^MX-SPK-/);
      await scheduler.getByRole('button', { name: '提交', exact: true }).click();
      await scheduler.getByRole('button', { name: '是', exact: true }).click();
      await scheduler.waitForTimeout(650);
      assert.match(await scheduler.locator('body').innerText(), /派生状态：待付款.*剩余课时：待核查/s);
      await scheduler.getByRole('button', { name: '录入收款' }).click();
      await scheduler.locator('.modal:visible input[data-fieldname="amount"]').fill('0.10');
      await scheduler.locator('.modal:visible select[data-fieldname="payment_method"]').selectOption('现金');
      await scheduler.getByRole('button', { name: '确认收款' }).click();
      await scheduler.waitForTimeout(650);
      assert.match(await scheduler.locator('body').innerText(), /已付净额：¥0\.10.*尚需付款：¥1\.13/s);
      await scheduler.getByRole('button', { name: '录入收款' }).click();
      await scheduler.locator('.modal:visible input[data-fieldname="amount"]').fill('1.14');
      await scheduler.locator('.modal:visible select[data-fieldname="payment_method"]').selectOption('银行转账');
      await scheduler.getByRole('button', { name: '确认收款' }).click();
      await scheduler.waitForTimeout(500);
      assert.match(await scheduler.locator('body').innerText(), /超额|不得超过/);
      const expectedOverpayment = errors.splice(0);
      assert(expectedOverpayment.length > 0);
      await scheduler.locator('.modal:visible .btn-modal-close').last().click();
      await scheduler.locator('.modal:visible input[data-fieldname="amount"]').fill('1.13');
      let paymentRequests = 0;
      scheduler.on('request', request => {
        if (request.url().includes('meixin_admin.payments.record_payment')) paymentRequests++;
      });
      await scheduler.getByRole('button', { name: '确认收款' }).dblclick();
      await scheduler.waitForTimeout(750);
      assert.equal(paymentRequests, 1);
      const paid = await scheduler.locator('body').innerText();
      assert.match(paid, /派生状态：生效.*已付净额：¥1\.23.*剩余课时：20/s);
      assert.equal((paid.match(/购买授予\s+20/g) || []).length, 1);
      assert.deepEqual(unexpectedErrors(errors), []);
      console.log(JSON.stringify({ phase: 'purchase', ...fixture, paymentRequests,
        expectedOverpaymentConsoleEvents: expectedOverpayment.length }));
    }
    if (!fixture.session) {
      const manager = await login(browser, 'm3-5d-manager@example.invalid');
      manager.on('pageerror', error => errors.push(`manager pageerror: ${error.message}`));
      manager.on('console', message => {
        if (['error', 'warning'].includes(message.type())) errors.push(`manager ${message.type()}: ${message.text()}`);
      });
      await manager.goto(`${base}/desk/mx-settings`, { waitUntil: 'domcontentloaded' });
      await manager.waitForTimeout(400);
      await manager.locator('[data-fieldname="present_rule"] select:visible').selectOption('课消');
      await manager.getByRole('button', { name: '保存', exact: true }).click();
      await manager.waitForTimeout(400);
      await newDoc(manager, 'mx-session');
      await link(manager, 'course', fixture.course, '5D虚构课程');
      await link(manager, 'teacher', fixture.teacher, '5D虚构教师');
      await link(manager, 'room', fixture.room, '5D虚构教室');
      await field(manager, 'start_at', '2026-09-24 08:00:00');
      await manager.locator('input[data-fieldname="start_at"]:visible').blur();
      await manager.waitForTimeout(400);
      assert.equal(await manager.locator('input[data-fieldname="end_at"]:visible').inputValue(), '2026-09-24 09:00:00');
      await manager.locator('[data-fieldname="students"] .grid-row[data-name] [data-fieldname="student"]').click();
      await link(manager, 'student', fixture.student, '5D虚构学生');
      fixture.session = await save(manager);
      await manager.getByRole('button', { name: '提交', exact: true }).click();
      await manager.getByRole('button', { name: '是', exact: true }).click();
      await manager.waitForTimeout(650);
      assert.match(await manager.locator('body').innerText(), /记录上课结果/);
      assert.deepEqual(unexpectedErrors(errors), []);
      console.log(JSON.stringify({ phase: 'session', ...fixture }));
    }
    if (!fixture.execution) {
      const scheduler = await login(browser, 'm3-5d-scheduler@example.invalid');
      scheduler.on('pageerror', error => errors.push(`scheduler pageerror: ${error.message}`));
      scheduler.on('console', message => {
        if (['error', 'warning'].includes(message.type())) errors.push(`scheduler ${message.type()}: ${message.text()}`);
      });
      await scheduler.goto(`${base}/desk/mx-session/${fixture.session}`, { waitUntil: 'domcontentloaded' });
      await scheduler.getByRole('button', { name: '记录上课结果' }).click();
      await scheduler.waitForTimeout(500);
      await scheduler.locator('[data-fieldname="attendance"] .grid-row[data-name] [data-fieldname="attendance_status"]').click();
      await scheduler.locator('select[data-fieldname="attendance_status"]:visible').selectOption('到课');
      fixture.execution = await save(scheduler);
      await scheduler.getByRole('button', { name: '提交', exact: true }).click();
      await scheduler.getByRole('button', { name: '是', exact: true }).click();
      await scheduler.waitForTimeout(650);
      await scheduler.goto(`${base}/desk/mx-student-package/${fixture.package}`, { waitUntil: 'domcontentloaded' });
      await scheduler.waitForTimeout(600);
      const consumed = await scheduler.locator('body').innerText();
      assert.match(consumed, /剩余课时：19/);
      assert.match(consumed, /购买授予\s+20/);
      assert.match(consumed, /M2 课消扣减\s+-1/);
      assert.deepEqual(unexpectedErrors(errors), []);
      console.log(JSON.stringify({ phase: 'consumption', ...fixture }));
    }
    if (!fixture.executionReversed) {
      const manager = await login(browser, 'm3-5d-manager@example.invalid');
      manager.on('pageerror', error => errors.push(`manager pageerror: ${error.message}`));
      manager.on('console', message => {
        if (['error', 'warning'].includes(message.type())) errors.push(`manager ${message.type()}: ${message.text()}`);
      });
      await manager.goto(`${base}/desk/mx-session-execution/${fixture.execution}`, { waitUntil: 'domcontentloaded' });
      await manager.waitForTimeout(450);
      await manager.getByRole('button', { name: '取消', exact: true }).click();
      await manager.getByRole('button', { name: '是', exact: true }).click();
      await manager.waitForTimeout(550);
      await manager.goto(`${base}/desk/mx-student-package/${fixture.package}`, { waitUntil: 'domcontentloaded' });
      await manager.waitForTimeout(550);
      const restored = await manager.locator('body').innerText();
      assert.match(restored, /剩余课时：20/);
      assert.match(restored, /M2 课消扣减\s+-1/);
      assert.match(restored, /M2 reversal 返还\s+1/);
      fixture.executionReversed = true;
      assert.deepEqual(unexpectedErrors(errors), []);
      console.log(JSON.stringify({ phase: 'execution-reversed', ...fixture }));
    }
    if (!fixture.refundReversed) {
      const manager = await login(browser, 'm3-5d-manager@example.invalid');
      manager.on('pageerror', error => errors.push(`manager pageerror: ${error.message}`));
      manager.on('console', message => {
        if (['error', 'warning'].includes(message.type())) errors.push(`manager ${message.type()}: ${message.text()}`);
      });
      await manager.goto(`${base}/desk/mx-student-package/${fixture.package}`, { waitUntil: 'domcontentloaded' });
      await manager.waitForTimeout(500);
      if (!fixture.adjusted) {
        await manager.getByRole('button', { name: '人工调整课时' }).click();
        await manager.locator('.modal:visible select[data-fieldname="direction"]').selectOption('减少');
        await manager.locator('.modal:visible input[data-fieldname="quantity"]').fill('1');
        await manager.locator('.modal:visible textarea[data-fieldname="reason"]').fill('5D验收：纠正多授一课时');
        await manager.getByRole('button', { name: '确认人工调整' }).dblclick();
        await manager.waitForTimeout(650);
        const adjusted = await manager.locator('body').innerText();
        assert.match(adjusted, /剩余课时：19/);
        assert.match(adjusted, /人工调整\s+-1.*5D验收：纠正多授一课时/s);
        fixture.adjusted = true;
        console.log(JSON.stringify({ phase: 'adjusted', ...fixture }));
      }
      if (!fixture.refund) {
        await manager.getByRole('button', { name: '退款关闭课包' }).click();
        await manager.locator('.modal:visible input[data-fieldname="amount"]').fill('1.00');
        await manager.locator('.modal:visible select[data-fieldname="payment_method"]').selectOption('现金');
        await manager.locator('.modal:visible textarea[data-fieldname="reason"]').fill('5D验收：关闭课包');
        await manager.getByRole('button', { name: '确认退款并关闭课包' }).click();
        await manager.waitForTimeout(650);
        const closed = await manager.locator('body').innerText();
        assert.match(closed, /派生状态：已退款关闭.*已付净额：¥0\.23.*剩余课时：0/s);
        assert.match(closed, /退款收回\s+-19/);
        assert.equal(await manager.getByRole('button', { name: '人工调整课时' }).count(), 0);
        assert.equal(await manager.getByRole('button', { name: '录入收款' }).count(), 0);
        fixture.refund = true;
        console.log(JSON.stringify({ phase: 'refund-closed', ...fixture }));
      }
      await manager.getByRole('button', { name: '撤销退款关闭' }).click();
      await manager.waitForTimeout(300);
      assert.match(await manager.locator('body').innerText(), /原收回课时 19/);
      await manager.locator('.modal:visible textarea[data-fieldname="reason"]').fill('5D验收：撤销错误退款');
      await manager.getByRole('button', { name: '确认撤销退款关闭' }).dblclick();
      await manager.waitForTimeout(700);
      const refundRestored = await manager.locator('body').innerText();
      assert.match(refundRestored, /派生状态：生效.*已付净额：¥1\.23.*剩余课时：19/s);
      assert.match(refundRestored, /退款 reversal 恢复\s+19/);
      assert.match(refundRestored, /M2 课消扣减\s+-1/);
      assert.match(refundRestored, /M2 reversal 返还\s+1/);
      fixture.refundReversed = true;
      assert.deepEqual(unexpectedErrors(errors), []);
      console.log(JSON.stringify({ phase: 'refund-reversed', ...fixture }));
    }
    if (!fixture.giftSubmitted) {
      const manager = await login(browser, 'm3-5d-manager@example.invalid');
      manager.on('pageerror', error => errors.push(`manager pageerror: ${error.message}`));
      manager.on('console', message => {
        if (['error', 'warning'].includes(message.type())) errors.push(`manager ${message.type()}: ${message.text()}`);
      });
      if (!fixture.gift) {
        await manager.goto(`${base}/desk/mx-student-package`, { waitUntil: 'domcontentloaded' });
        await manager.getByRole('button', { name: '赠送课包' }).click();
        await manager.waitForTimeout(750);
        await link(manager, 'student', fixture.student, '5D虚构学生');
        await manager.waitForTimeout(350);
        await link(manager, 'package_plan', fixture.plan, '5D虚构课包');
        await manager.waitForTimeout(350);
        await field(manager, 'expires_on', '2026-09-25');
        await manager.locator('input[data-fieldname="expires_on"]:visible').blur();
        await manager.waitForTimeout(350);
        console.log(JSON.stringify({ phase: 'gift-input-check',
          student: await manager.locator('input[data-fieldname="student"]:visible').inputValue(),
          plan: await manager.locator('input[data-fieldname="package_plan"]:visible').inputValue(),
          expires: await manager.locator('input[data-fieldname="expires_on"]:visible').inputValue() }));
        assert.match(await manager.locator('body').innerText(), /赠送成交金额为 ¥0\.00，不产生收款/);
        await manager.getByRole('button', { name: '创建赠送草稿' }).click();
        await manager.waitForTimeout(550);
        fixture.gift = manager.url().split('/').at(-1);
        const draft = await manager.locator('body').innerText();
        assert.match(draft, /暂无付款记录/);
        assert.match(draft, /暂无权益流水/);
        console.log(JSON.stringify({ phase: 'gift-draft', ...fixture }));
      } else {
        await manager.goto(`${base}/desk/mx-student-package/${fixture.gift}`, { waitUntil: 'domcontentloaded' });
      }
      await manager.getByRole('button', { name: '提交', exact: true }).click();
      await manager.getByRole('button', { name: '是', exact: true }).click();
      await manager.waitForTimeout(700);
      const gifted = await manager.locator('body').innerText();
      assert.match(gifted, /获取类型\s+赠送/);
      assert.match(gifted, /派生状态：生效.*应收：¥0\.00.*已付净额：¥0\.00.*剩余课时：20/s);
      assert.match(gifted, /暂无付款记录/);
      assert.equal((gifted.match(/赠送授予\s+20/g) || []).length, 1);
      fixture.giftSubmitted = true;
      assert.deepEqual(unexpectedErrors(errors), []);
      console.log(JSON.stringify({ phase: 'gift-submitted', ...fixture }));
    }
    if (!fixture.permissionsChecked) {
      const scheduler = await login(browser, 'm3-5d-scheduler@example.invalid');
      const deniedResponses = [];
      const permissionConsole = [];
      const recordPermissionConsole = (page, role) => {
        page.on('pageerror', error => permissionConsole.push(`${role} pageerror: ${error.message}`));
        page.on('console', message => {
          if (['error', 'warning'].includes(message.type())) {
            permissionConsole.push(`${role} ${message.type()}: ${message.text()}`);
          }
        });
      };
      recordPermissionConsole(scheduler, 'scheduler');
      scheduler.on('response', response => {
        if (response.url().includes('/api/method/') && response.status() >= 400) {
          deniedResponses.push({ method: response.url().split('/').at(-1), status: response.status() });
        }
      });
      await scheduler.goto(`${base}/desk/mx-student-package/${fixture.package}`, { waitUntil: 'domcontentloaded' });
      await scheduler.waitForTimeout(550);
      assert.equal(await scheduler.getByRole('button', { name: '人工调整课时' }).count(), 0);
      assert.equal(await scheduler.getByRole('button', { name: '退款关闭课包' }).count(), 0);
      assert.equal(await scheduler.getByRole('button', { name: '赠送课包' }).count(), 0);
      const schedulerAdjust = await directApi(scheduler, 'meixin_admin.entitlements.adjust_credits', {
        student_package: fixture.package, effect: 1, reason: '越权测试', request_id: '5d-deny-adjust',
      });
      const schedulerGift = await directApi(scheduler, 'meixin_admin.entitlements.create_student_package', {
        student: fixture.student, package_plan: fixture.plan, acquisition_type: '赠送',
        effective_from: '2026-09-24', request_id: '5d-deny-gift',
      });
      const schedulerRefund = await directApi(scheduler, 'meixin_admin.payments.refund_close_package', {
        student_package: fixture.package, amount: '0.01', reason: '越权测试', request_id: '5d-deny-refund',
      });
      const restricted = await login(browser, 'm3-5d-restricted@example.invalid');
      recordPermissionConsole(restricted, 'restricted');
      await restricted.goto(`${base}/desk/query-report/学生课包概览`, { waitUntil: 'domcontentloaded' });
      await restricted.waitForTimeout(950);
      const reportHasPackage = (await restricted.locator('body').innerText()).includes(fixture.package);
      await restricted.goto(`${base}/desk/mx-student-package/${fixture.package}`, { waitUntil: 'domcontentloaded' });
      await restricted.waitForTimeout(550);
      const restrictedHasBalance = (await restricted.locator('body').innerText()).includes('剩余课时：19');
      const restrictedApi = await restricted.request.get(`${base}/api/method/meixin_admin.entitlements.package_overview?student_package=${fixture.package}`);
      const guest = await browser.newPage();
      const guestApi = await guest.request.get(`${base}/api/method/meixin_admin.entitlements.package_overview?student_package=${fixture.package}`);
      const manager = await login(browser, 'm3-5d-manager@example.invalid');
      recordPermissionConsole(manager, 'manager');
      manager.on('response', response => {
        if (response.url().includes('/api/method/') && response.status() >= 400) {
          deniedResponses.push({ method: response.url().split('/').at(-1), status: response.status() });
        }
      });
      const directCredit = await directApi(manager, 'frappe.client.insert', { doc: JSON.stringify({
        doctype: 'MX Lesson Credit Entry', student: fixture.student,
        student_package: fixture.package, package_plan: fixture.plan, course: fixture.course,
        operation_type: '人工调整', effect: 1, idempotency_key: '5d-direct-credit-denied',
      }) });
      assert.equal(schedulerAdjust.ok, false);
      assert.equal(schedulerGift.ok, false);
      assert.equal(schedulerRefund.ok, false);
      assert.equal(directCredit.ok, false);
      assert.equal(reportHasPackage, false);
      assert.equal(restrictedHasBalance, false);
      assert.equal(restrictedApi.status(), 403);
      assert.equal(guestApi.status(), 403);
      assert.equal(deniedResponses.length, 4);
      fixture.permissionsChecked = true;
      console.log(JSON.stringify({ phase: 'permissions', schedulerAdjust, schedulerGift, schedulerRefund, directCredit,
        reportHasPackage, restrictedHasBalance, restrictedApiStatus: restrictedApi.status(),
        guestApiStatus: guestApi.status(), deniedResponses, expectedPermissionConsoleEvents: permissionConsole.length,
        permissionConsoleKinds: permissionConsole.map(item => item.includes('403') || item.includes('PermissionError') ?
          'expected 403' : item.includes('was preloaded using link preload') ? 'Frappe preload warning' : item.slice(0, 100)),
        knownFrappePreloadWarnings: errors.length, unexpectedConsoleEvents: unexpectedErrors(errors) }));
    }
    if (!fixture.fefoChecked) {
      const manager = await login(browser, 'm3-5d-manager@example.invalid');
      manager.on('pageerror', error => errors.push(`FEFO pageerror: ${error.message}`));
      manager.on('console', message => {
        if (['error', 'warning'].includes(message.type())) errors.push(`FEFO ${message.type()}: ${message.text()}`);
      });
      const result = await consumeNewSession(manager, fixture, '2026-09-24 09:00:00', '5D FEFO 提前完成');
      await manager.goto(`${base}/desk/mx-student-package/${fixture.gift}`, { waitUntil: 'domcontentloaded' });
      await manager.waitForTimeout(500);
      assert.match(await manager.locator('body').innerText(), /剩余课时：19.*M2 课消扣减\s+-1/s);
      await manager.goto(`${base}/desk/mx-student-package/${fixture.package}`, { waitUntil: 'domcontentloaded' });
      await manager.waitForTimeout(500);
      assert.match(await manager.locator('body').innerText(), /剩余课时：19/);
      assert.deepEqual(unexpectedErrors(errors), []);
      fixture.fefoChecked = true;
      console.log(JSON.stringify({ phase: 'fefo', ...fixture, ...result }));
    }
    if (!fixture.fifoChecked) {
      const manager = await login(browser, 'm3-5d-manager@example.invalid');
      manager.on('pageerror', error => errors.push(`FIFO pageerror: ${error.message}`));
      manager.on('console', message => {
        if (['error', 'warning'].includes(message.type())) errors.push(`FIFO ${message.type()}: ${message.text()}`);
      });
      if (!fixture.gift2) {
        await manager.goto(`${base}/desk/mx-student-package`, { waitUntil: 'domcontentloaded' });
        await manager.getByRole('button', { name: '赠送课包' }).click();
        await manager.waitForTimeout(750);
        await link(manager, 'student', fixture.student, '5D虚构学生');
        await manager.waitForTimeout(350);
        await link(manager, 'package_plan', fixture.plan, '5D虚构课包');
        await manager.waitForTimeout(350);
        await manager.getByRole('button', { name: '创建赠送草稿' }).click();
        await manager.waitForTimeout(550);
        fixture.gift2 = manager.url().split('/').at(-1);
        await manager.getByRole('button', { name: '提交', exact: true }).click();
        await manager.getByRole('button', { name: '是', exact: true }).click();
        await manager.waitForTimeout(550);
        console.log(JSON.stringify({ phase: 'fifo-candidate', gift2: fixture.gift2 }));
      }
      const result = await consumeNewSession(manager, fixture, '2026-09-26 09:00:00', '5D FIFO 提前完成');
      await manager.goto(`${base}/desk/mx-student-package/${fixture.package}`, { waitUntil: 'domcontentloaded' });
      await manager.waitForTimeout(500);
      assert.match(await manager.locator('body').innerText(), /剩余课时：18/);
      await manager.goto(`${base}/desk/mx-student-package/${fixture.gift2}`, { waitUntil: 'domcontentloaded' });
      await manager.waitForTimeout(500);
      assert.match(await manager.locator('body').innerText(), /剩余课时：20/);
      assert.deepEqual(unexpectedErrors(errors), []);
      fixture.fifoChecked = true;
      console.log(JSON.stringify({ phase: 'fifo', ...fixture, ...result }));
    }
    if (!fixture.supplementChecked) {
      const manager = await login(browser, 'm3-5d-manager@example.invalid');
      manager.on('pageerror', error => errors.push(`manager pageerror: ${error.message}`));
      manager.on('console', message => {
        if (['error', 'warning'].includes(message.type())) errors.push(`manager ${message.type()}: ${message.text()}`);
      });
      await manager.goto(`${base}/desk/mx-student-package/${fixture.package}`, { waitUntil: 'domcontentloaded' });
      await manager.waitForTimeout(500);
      await manager.locator('tr').filter({ hasText: '¥0.10' }).locator('.mx-reverse-payment').click();
      await manager.waitForTimeout(250);
      await manager.locator('.modal:visible textarea[data-fieldname="reason"]').fill('5D验收：撤销错录的首付款');
      await manager.getByRole('button', { name: '确认撤销收款' }).click();
      await manager.waitForTimeout(650);
      const frozen = await manager.locator('body').innerText();
      assert.match(frozen, /派生状态：欠费冻结.*已付净额：¥1\.13.*剩余课时：18/s);
      const scheduler = await login(browser, 'm3-5d-scheduler@example.invalid');
      scheduler.on('pageerror', error => errors.push(`scheduler pageerror: ${error.message}`));
      scheduler.on('console', message => {
        if (['error', 'warning'].includes(message.type())) errors.push(`scheduler ${message.type()}: ${message.text()}`);
      });
      await scheduler.goto(`${base}/desk/mx-student-package/${fixture.package}`, { waitUntil: 'domcontentloaded' });
      await scheduler.getByRole('button', { name: '录入收款' }).click();
      await scheduler.locator('.modal:visible input[data-fieldname="amount"]').fill('0.10');
      await scheduler.locator('.modal:visible select[data-fieldname="payment_method"]').selectOption('现金');
      await scheduler.getByRole('button', { name: '确认收款' }).click();
      await scheduler.waitForTimeout(650);
      const restored = await scheduler.locator('body').innerText();
      assert.match(restored, /派生状态：生效.*已付净额：¥1\.23.*剩余课时：18/s);
      assert.equal((restored.match(/购买授予\s+20/g) || []).length, 1);
      fixture.supplementChecked = true;
      assert.deepEqual(unexpectedErrors(errors), []);
      console.log(JSON.stringify({ phase: 'supplement-no-double-grant', ...fixture }));
    }
    if (fixture.supplementChecked) {
      const manager = await login(browser, 'm3-5d-manager@example.invalid');
      manager.on('pageerror', error => errors.push(`manager pageerror: ${error.message}`));
      manager.on('console', message => {
        if (['error', 'warning'].includes(message.type())) errors.push(`manager ${message.type()}: ${message.text()}`);
      });
      await manager.goto(`${base}/desk/mx-student-package/${fixture.package}`, { waitUntil: 'domcontentloaded' });
      await manager.waitForTimeout(500);
      const body = await manager.locator('body').innerText();
      assert.match(body, /派生状态：生效.*已付净额：¥1\.23.*剩余课时：18/s);
      assert.equal((body.match(/购买授予\s+20/g) || []).length, 1);
      assert.deepEqual(unexpectedErrors(errors), []);
      console.log(JSON.stringify({ phase: 'acceptance-complete', ...fixture,
        knownFrappePreloadWarnings: errors.length, unexpectedConsoleEvents: unexpectedErrors(errors) }));
    }
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
