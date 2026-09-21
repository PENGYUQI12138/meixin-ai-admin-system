# M2 验收记录

最后更新：2026-09-21（Asia/Shanghai）

## 当前结论

阶段 1 至阶段 7 已完成。源码审查、静态检查、隔离数据库回归、并发、权限、回滚、清理、唯一索引、Scheduler / Manager 隔离浏览器流程以及正式站只读浏览器验收均通过。

正式 `frontend` 已运行 M2 final 镜像 `meixin-admin:m2-final-frappe16.34.0`。正式 migrate、健康检查、数据一致性检查和只读冒烟均通过，正式站没有创建测试业务记录。

## 自动化结果

仅在 `test_meixin_m1.localhost` 执行：

```text
Ran 52 tests in 14.670s
OK
{"site": "test_meixin_m1.localhost", "tests_run": 52, "failures": 0, "errors": 0, "skipped": 0}
```

- M1 回归：30/30。
- M2：22/22。
- M2 覆盖三态规则、四种考勤、`0` 流水、提前完成、名单冻结、单一有效执行、自动/人工 reversal、修订、课程取消、不可变历史、规则冻结、事务回滚、三项并发、幂等键冲突、角色权限和 User Permission 隔离。
- 审查新增的“先人工撤销、再取消整张执行单”组合路径验证：已有 reversal 被复用，未产生第二条 `-1`。

补充权限测试首次运行结果为 51 项通过、1 项测试夹具错误：测试在外部用户身份下创建权限夹具，被应用权限正确拒绝。测试切回 Administrator 创建夹具后，完整 52 项通过；未把该次失败改写为业务通过。

## 数据库与清理核对

- 隔离站 `TEST-M2-%` 执行单：0。
- 隔离站 `TEST-M2-%` 课消流水：0。
- 五项课消规则测试后均恢复“未配置”。
- MariaDB `tabMX Lesson Consumption Entry.idempotency_key`：`Non_unique=0`、BTREE。
- 正式数据库已存在三个 M2 DocType，三类 M2 业务记录均为 0；`idempotency_key` 为 `Non_unique=0` 的唯一 BTREE。

## 静态与结构检查

- Python `compileall`：通过。
- 全部 JSON 解析：通过。
- 三个 M2 相关 DocType JavaScript `node --check`：通过。
- `git diff --check`：通过。
- 未新增第三方依赖、锁服务、余额或收费模型。

## 真实浏览器验收

验收前只读检查：隔离站主页、登录页、`/api/method/ping` 和 Socket.IO 轮询均为 HTTP 200；四个隔离容器运行，MariaDB healthy、重启次数为 0，最近应用和入口日志无错误。由此确认早前的 `nodeRepl.fetch request failed` 属于浏览器控制工具链，不是应用故障。重置控制会话并新建 IAB 标签后恢复验收能力；Edge 独立会话仍失败，因此使用同一隔离站的独立 Cookie 域登录专用 Scheduler 账号。

实际点击结果：

1. Workspace 显示“上课执行”和“课消流水”。
2. Scheduler 从已确认排课点击“记录上课结果”，服务器名单自动带入；到课、请假、缺勤、其他均能选择。
3. “其他”不填说明时明确拒绝；补充说明后，规则未配置时明确提示 Manager 先配置。
4. Manager 在机构设置配置到课=`课消`、请假=`不课消`、缺勤=`课消`、其他=`不课消`、课程取消=`不课消`；Scheduler 随后正常提交，考勤行冻结对应规则。
5. 流水列表显示四条决定：到课 `+1`、请假 `0`、缺勤 `+1`、其他 `0`。Scheduler 详情和菜单均无修改、删除或人工撤销入口，已提交执行单也无取消或修订入口。
6. Scheduler 对未来排课提交被明确拒绝；Manager 填写提前完成原因后提交成功，完成原因、完成人和时间在详情中可见。
7. Manager 对到课 `+1` 追加人工 `-1`，随后取消整张执行单。最终 4 条决定和 4 条 reversal 共 8 条：已有人工 reversal 被复用，没有第二条 `-1`；另一条 `+1` 自动产生 `-1`，两条 `0` 均产生带 `reversal_of` 的 `0` 撤销。
8. 已取消执行单出现“修订”按钮，并成功进入 `amended_from=MX-EXE-00044` 的新草稿；该草稿未保存。
9. 全新标签逐页打开 Workspace、执行列表、执行详情、流水列表和 reversal 详情，无明显显示异常；最终浏览器控制台实际读取 `0 error / 0 warn`。

预期的三次业务拒绝（其他说明缺失、规则未配置、Scheduler 提前完成）在原流程标签中由 Frappe 记录为请求异常，同时页面均显示正确中文业务提示；最终无失败请求的全新验收标签控制台为 0/0。

## 浏览器验收清理

- 删除批次 `BROWSER-M2-20260921-1355` 的排课、执行单、考勤、流水、学生、教师、课程和教室；七类带批次主记录复核均为 0。
- 删除专用 Scheduler 测试账号；复核用户计数为 0。
- 五项课消规则全部恢复“未配置”。
- 清理后隔离 `/api/method/ping` 仍返回 `pong` / HTTP 200。
- Git 工作区在文档更新前保持干净，业务代码未因浏览器工具故障修改。

## 正式部署门禁

正式部署门禁已通过：

- 部署前备份：`D:\FrappeProject\backups\meixin-m2-predeploy-20260921_142856`。数据库 gzip、public/private tar、站点与环境 JSON 可读取，SHA256 全部复核一致。
- 镜像：`meixin-admin:m2-final-frappe16.34.0`，ID `sha256:35f61720d2d17494011b4d0bb23fcfa15fdc0704f37cc756191da32780a0b30b`；80 个 Git 跟踪文件与 candidate `603807a` 逐文件一致。
- 仅重建六个应用容器；MariaDB、Redis、sites、logs 和全部持久化卷保持原实例。正式 migrate 与 clear-cache 各一次，退出码 0；未运行 install-app、configurator 或 create-site。
- schema 已包含 MX Session Execution、MX Session Attendance、MX Lesson Consumption Entry、五项规则字段、权限/Workspace 元数据和 `idempotency_key` 唯一索引。
- 正式既有 Single 记录对新增 Select 字段没有自动持久化默认值。经用户明确授权，仅对五个空字段通过 `frappe.get_single("MX Settings")` 的正常 Document `set/save` 机制写入“未配置”；所有字段写入前均为空，机构名称、时区和其他设置未修改。
- 正式浏览器实际确认五项规则均选中“未配置”，Workspace、M1 七个入口、M2 两个入口、Execution/Attendance/Consumption 页面正常；全新标签控制台 `0 error / 0 warn`。
- 六个应用容器运行同一 M2 final 镜像、重启数 0；MariaDB healthy、Redis PONG，主页、ping、Socket.IO 均 HTTP 200。
- Administrator 与业务用户均为 `Asia/Chongqing`。部署前后九类 MX 业务记录均为 0，没有测试 Execution、Attendance 或 Consumption Entry。
- 隔离测试结果保持 M1 30/30、M2 22/22、合计 52/52；正式站未运行测试套件。

回滚基线为 M1 镜像 `meixin-admin:m1-final-frappe16.34.0` 和上述同批部署前完整备份。由于 migrate 已改变 schema，若需完整回退，必须停写并经明确确认后成套恢复数据库与文件；只退应用镜像不等于数据库已回退。
