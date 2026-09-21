# M2 验收记录

最后更新：2026-09-21（Asia/Shanghai）

## 当前结论

阶段 1 至阶段 4 已形成独立 checkpoint。阶段 5 的源码审查、静态检查、隔离数据库回归、并发、权限、回滚、清理和唯一索引核对已通过；真实浏览器点击与控制台验收因浏览器控制通道返回 `nodeRepl.fetch request failed` 尚未完成，不能记为通过。

正式 `frontend` 仍运行 M1 镜像 `meixin-admin:m1-final-frappe16.34.0`。M2 未在正式站点 migrate、重建容器或写入数据。

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
- 正式六个应用容器仍为 M1 final 镜像；正式数据库未执行 M2 migrate。

## 静态与结构检查

- Python `compileall`：通过。
- 全部 JSON 解析：通过。
- 三个 M2 相关 DocType JavaScript `node --check`：通过。
- `git diff --check`：通过。
- 未新增第三方依赖、锁服务、余额或收费模型。

## 待完成门禁

恢复可用的真实浏览器自动化通道后，在隔离站分别用 Scheduler 和 Manager 验证：

1. 从已确认排课进入执行单，名单自动带入。
2. 未配置规则时提交被拒绝并显示中文指引。
3. Scheduler 正常时间提交、提前提交被拒绝。
4. Manager 有原因提前提交、取消、修订和人工撤销。
5. 执行状态、考勤冻结、`+1/0/-1` 流水及来源链接显示正确。
6. 全新页面控制台 0 error、0 warn。

该门禁完成前不宣称 M2 浏览器验收通过，也不进入正式部署。
