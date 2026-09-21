# 美心行政 M3 设计

最后更新：2026-09-21（Asia/Shanghai）

## 目标、基线与边界

M3 只建立“学生报名/购买课包 → 缴费 → 获得课时权益 → M2 课消扣减 → 剩余课时 → 续费”的闭环。

稳定基线为 M2 final `615316ee991a172f198a378b2fa33db2ae60b000`；本地 `main`、`origin/main` 与 GitHub `main` 均指向该提交。M2 已正式部署并冻结，GitHub 仓库已转为 Private。M3 仅在 `m3-enrollment-payment-package` 分支前进，不 rebase、reset、amend、force push 或改写 M1/M2 历史。

M3 不实现教师工资、总账、会计凭证、支付渠道集成、家长端、发票、CRM、营销、AI 收费或大型经营报表。正式 `frontend` 在候选 checkpoint、完整备份和用户明确确认前，不 migrate、不重建、不写入测试业务数据。

## 不变量与优先级

实现优先级固定为：

```text
数据一致性 > 审计性 > 幂等性 > 权限 > 业务可解释性 > UI 便利性
```

现金、课时权益和 M2 课消事实是三个独立概念：

- `MX Payment` 记录实际现金效果。
- `MX Lesson Credit Entry` 记录学生拥有和消耗的课时权益。
- `MX Lesson Consumption Entry` 继续记录 M2 已确认的课消事实。

金额不换算为课消次数。`MX Student Package` 不保存可人工编辑的 `balance`；余额始终按 `SUM(MX Lesson Credit Entry.effect)` 派生。页面余额只用于展示，真正扣减、退款关闭或负调整时必须在事务锁内重新汇总，不能信任客户端值或此前读取值。

## 数据关系

```text
MX Package Plan（产品模板）
  └── MX Student Package（一次购买、续费或赠送实例）
        ├── MX Payment（不可覆盖的现金业务流水）
        └── MX Lesson Credit Entry（不可变课时权益流水）
                └── M2 MX Lesson Consumption Entry（扣减/返还来源）
```

M3 采用四个核心 DocType 和两个服务模块：

- `MX Package Plan`
- `MX Student Package`
- `MX Payment`
- `MX Lesson Credit Entry`
- `meixin_admin/entitlements.py`
- `meixin_admin/payments.py`

不增加独立 Allocation DocType。一次 M2 有效课消固定扣 1 课时，Credit Entry 同时承担分配结果和审计记录。

## MX Package Plan

课包产品模板，不代表学生已购买。建议字段：

- 课包名称
- 精确适用 `MX Course`
- 标准课时数（正整数）
- 标准价格（Currency）
- 币种，M3 默认并限定为人民币 `CNY`
- 是否启用
- 说明

M3 只实现精确 Course 匹配，不实现学科级、多课程组合或可兑换课时。原因是 M2 冻结了 Course，但没有冻结可编辑的学科；使用当前学科匹配会使历史语义漂移。

Manager 可以维护 Package Plan，Scheduler 只读。修改计划只影响后续购买，不回写任何已有 Student Package 快照。

## MX Student Package

每次报名、购买、续费或赠送都创建新的 Student Package，不修改旧包的购买数量或成交金额。

建议使用 Frappe `docstatus` 生命周期，字段至少包括：

- 学生
- Package Plan
- 获取类型：购买、赠送
- 产品名称、课程、课程名称快照
- 获得课时数快照
- 成交金额和币种快照
- 生效日期、可选失效日期
- 原始报名/购买来源说明
- 系统生成且只读的 `request_id`，数据库唯一
- 首次激活时间，只读
- `amended_from`

购买型课包允许分次付款。净收款不足成交金额时为“待付款”；净收款恰好达到成交金额时，在完成该笔付款的同一事务内一次性生成完整 `+N` 初始课时权益。不得按付款比例拆分权益，禁止超额付款。

赠送包只允许 Manager 提交，成交金额为 0，并在提交事务内直接授予完整权益。M3 不静默加入授信、欠费先上课或负余额能力。

Student Package 的业务状态均派生，不提供普通可编辑状态字段：

| 状态 | 派生条件 |
|---|---|
| 草稿 | `docstatus=0` |
| 待付款 | 已提交的购买包，尚未生成初始权益 |
| 生效 | 已授予权益、付款满足成交金额、日期有效、未退款关闭且余额大于 0 |
| 已耗尽 | 已授予权益且余额为 0 |
| 已过期 | 上课计划开始时间晚于失效日期 |
| 欠费冻结 | 已授予权益，但收款 reversal 使净付款低于成交金额 |
| 已退款关闭 | 存在尚未撤销的“退款关闭课包”记录 |
| 已取消 | 仅在无付款、无权益流水时允许 Manager 取消 |

## MX Payment 与资金流水

`MX Payment` 是可提交业务单，也是 M3 的不可覆盖资金流水；不再创建第二套 Cash Ledger。

建议字段至少包括：

- 学生和 Student Package
- 操作类型：收款、退款关闭课包、撤销
- 金额（始终录入正数）
- 服务器派生的只读 `cash_effect`
- 币种
- 支付方式、支付时间、经办人
- `reversal_of`
- 原因
- 退款关闭时收回的剩余课时数快照
- 系统生成且只读的 `request_id`，数据库唯一

规则：

- Scheduler 只能创建和提交普通收款；Manager 才能执行退款关闭或撤销。
- 已提交 Payment 不得普通编辑、取消或删除；纠错只追加撤销记录。
- `cash_effect` 必须由服务器按操作类型推导，不接受客户端传入值。
- 收款为正；退款关闭为负；撤销为被撤销 Payment 的相反效果。
- `reversal_of` 建数据库唯一约束；一条 Payment 最多被撤销一次。
- 同一 Student Package 的所有 Payment 必须与购买快照使用同一币种。M3 仅支持 CNY，不实现外币、汇率或币种兑换。
- 所有金额运算使用 Frappe/MariaDB Decimal/Currency 精度，禁止 Python float 比较。
- 净付款在现有 MariaDB 写锁内按已提交 Payment 的 `cash_effect` 重新汇总；结果不得小于 0，收款后不得超过成交金额。
- 收款 reversal 导致净付款低于成交金额时，不回写或删除已授予权益，也不恢复已消费课时；该包进入派生的“欠费冻结”，补足付款前不得继续扣课。

### 退款关闭课包

M3 不实现通用的“部分退款后继续使用”、按单价折算或自动比例回收。所有 UI、字段说明、测试和文档统一使用名称“退款关闭课包”，不得只显示含义模糊的“退款”。

退款关闭课包的语义：

- 只允许 Manager 执行。
- 退款金额不得超过锁内重新汇总的有效净收款。
- 在同一事务内重新计算并收回该包当前全部剩余权益。
- 已经真实消费的历史课时不恢复，不产生负余额。
- 生成新的负现金 Payment 和负课时 Credit Entry，旧记录不覆盖、不删除。
- Student Package 进入派生的“已退款关闭”，后续不得再被选为扣减候选。
- 错误退款只能新增 reversal；reversal 同时恢复原退款的现金效果和被收回的权益。
- 同一课包同一时刻最多存在一个未撤销的退款关闭记录。

## MX Lesson Credit Entry

课时权益流水不可变。任何角色都不得直接新增、普通修改、取消或删除，只能由受控业务服务生成：

- 购买授予 `+N`
- 赠送授予 `+N`
- M2 课消扣减 `-1`
- M2 reversal 返还 `+1`
- 退款关闭收回 `-N`
- 退款 reversal 恢复 `+N`
- Manager 人工调整，非零正负整数

建议字段至少包括：

- 学生和 Student Package
- Package Plan、Course 及名称快照
- 操作类型
- `effect`，不允许 0
- 来源 DocType、来源单号
- `m2_consumption_entry`
- `reversal_of`
- 原因
- 系统 `idempotency_key`
- Frappe 内置创建人和创建时间

系统幂等字段不得作为行政人员日常自由填写或修改的普通字段。数据库唯一保障包括：

- `idempotency_key`
- 非空 `m2_consumption_entry`
- 非空 `reversal_of`

MariaDB 允许唯一索引中存在多个 `NULL`，因此可在保留可选 Link 的同时约束每个非空来源最多一条流水。

人工调整只允许 Manager，必须填写理由。负调整必须在锁内重算余额，并拒绝任何会把包余额降到 0 以下的操作。旧流水永不覆盖。

## M2 → M3 同事务联动

只在冻结的 `meixin_admin/consumption.py` 增加窄集成点，不重构 M2：

```text
M2 create_decision effect=+1
  → M3 为选定 Student Package 生成 -1 Credit Entry

M2 effect=0
  → 不产生 Credit Entry

M2 create_reversal effect=-1
  → 对原 M3 扣减生成 +1 Credit Entry，并返还原 Student Package
```

M2 Consumption Entry、M3 Credit Entry 和 Execution 状态必须处于同一个 Frappe 请求事务。业务服务不得手动 commit。任何一步失败都抛出异常，由 Frappe 回滚整个请求，不能留下部分 Consumption、Credit 或 Execution 状态。

建议幂等键：

```text
package-grant:<student-package>
m2-consume:<m2-consumption-entry>
m2-restore:<m2-reversal-entry>
payment-refund-reclaim:<payment>
payment-reversal-restore:<payment-reversal>
manual-adjustment:<request-id>
```

M2 reversal 必须查找原 `-1` Credit Entry，并把 `+1` 返还到同一个 Student Package，不能重新运行课包选择算法。

## 多课包选择与扣减

所有候选判断、付款汇总和余额汇总都必须在现有写锁内执行。

合法候选同时满足：

- 同一学生；
- Student Package 快照中的 Course 与 M2 Consumption Entry Course 精确匹配；
- 已产生初始权益；
- 购买型净付款满足成交金额，或为合法赠送包；
- 未退款关闭；
- M2 冻结的上课计划开始时间落在生效/失效期间；
- 锁内派生余额至少为 1。

有效期按站点业务日解释，使用 M2 流水冻结的原排课计划开始时间，而不是执行提交时间、流水创建时间或当前时间。`effective_from` 和 `expires_on` 都是 Date：生效日当天可用，失效日当天也可用，次日才算过期；使用 Frappe 站点时区解析日期，不硬编码 UTC 偏移。

购买包若已经授予权益，但锁内汇总的有效净付款低于成交金额，则派生为“欠费冻结”：保留既有权益和历史课消，只禁止新的扣减；补足付款后自动恢复候选资格，原 `package-grant:<student-package>` 保证不会重复授予。存在尚未被 Payment reversal 撤销的“退款关闭课包”流水时，该包不得进入候选；4C 只实现这一候选判断，退款及其 reversal 业务流程仍留在 4D。

稳定选择顺序：

1. 最早失效优先；
2. 未设置失效日期排最后；
3. 最早激活优先；
4. Student Package `name` 作为最终稳定排序。

无候选包时，拒绝整个 M2 执行提交，不创建异步悬账，不提供强制扣负数按钮。错误必须按实际数据明确区分：

- 没有适用课程的课包；
- 购买课包尚未付清；
- 课包余额已耗尽；
- 课包尚未生效；
- 课包已过期；
- 课包处于欠费冻结；
- 课包已经退款关闭；
- 初始 grant、激活时间或有效期元数据异常。

提示行政通过正常缴费/补款、续费/购买新包、Manager 赠送，或由 Manager 检查异常历史数据后重新提交执行单。4D 完成后还可使用带理由的合法权益调整；4C 不提前开放该能力。错误只返回状态类别和处理建议，不返回其他课包编号、金额或无权查看的内部明细。

## 负余额与排课

M3 禁止负课时：

- 余额为 0 不阻止 M1 创建未来排课；界面可展示预警，但不改动 M1 排课不变量。
- M2 真正产生 `+1` 课消时必须存在余额至少为 1 的合法候选包。
- Scheduler 和 Manager 都不能把包强制扣成负数。
- 例外只能通过正常补款、续费、Manager 赠送或带理由的正权益调整处理。

以后如需“欠课”，必须设计独立责任模型，不把它隐含为负余额。

## 锁、幂等与并发

M3 继续复用 M1/M2 的 MariaDB 站点级 `SELECT ... FOR UPDATE` 写锁，不增加 Redis 锁、分布式锁或第二套锁顺序。

以下写操作都取得同一把锁：

- Package Plan 修改；
- Student Package 提交/取消；
- 收款、退款关闭和 Payment reversal；
- 权益授予和人工调整；
- M2 课消扣减和 reversal 返还。

请求内先锁定，再读取当前 Payment/Credit 汇总，再验证并写入。数据库唯一约束是 API 重试和并发请求的最终兜底。余额仅剩 1 时两个并发课消最多一个成功；另一个必须重新读取到 0 并整体失败。

`Student Package.request_id`、`Payment.request_id` 和 `Credit Entry.idempotency_key` 由客户端一次性生成或服务器受控生成，表单只读、`no_copy`、不可普通修改，并建立数据库唯一约束。相同键、相同内容返回原结果；相同键、不同内容拒绝。

## 权限

| 能力 | Meixin Manager | Meixin Scheduler |
|---|---:|---:|
| 查看 Plan、Student Package、Payment、Credit Entry | 是 | 是 |
| 创建、保存、提交普通购买/续费 | 是 | 是 |
| 录入并提交普通收款 | 是 | 是 |
| 修改 Package Plan | 是 | 否 |
| 提交赠送包 | 是 | 否 |
| 撤销 Payment | 是 | 否 |
| 退款关闭课包及撤销退款 | 是 | 否 |
| 人工调整课时 | 是 | 否 |
| 直接写入或删除 Credit Entry | 否 | 否 |

新 DocType 继续使用 DocPerm、服务端角色 gate、User Permission、permission query hooks 和禁止 DocShare。Guest、无业务角色用户、临时分享和直接 API 越权均必须拒绝。内部 SQL 可以用于锁定和完整性判断，但不得用来绕过入口单据和学生的权限检查或向调用者泄露无权查看的数据。

`Administrator` 保留 Frappe 超级用户能力，但 Payment 和 Credit Entry 控制器仍执行不可变、幂等、余额和历史保护规则。

## 测试计划

M1 30 项和 M2 22 项必须继续完整回归。M3 至少覆盖：

- Package Plan 创建、修改、停用和历史快照；
- Student Package 购买、赠送、一次付款、分次付款、续费；
- 满款只授予一次权益、禁止比例授予、禁止超额付款；
- request ID 重试、双击付款和不同内容幂等冲突；
- 金额 Decimal 精度、币种一致和服务器推导 cash effect；
- M2 `+1` 正常扣课、`0` 不扣课、`-1` 正常返还；
- 重复 M2 reversal 不重复返还；
- 多课包 FEFO/FIFO 稳定分配；
- 无适用课程、未付清、耗尽、过期、欠费冻结和退款关闭六类错误；
- 禁止负余额和无异步悬账；
- Payment reversal 与欠费冻结/补款恢复；
- 退款关闭收回全部剩余权益；
- 已消费课时不因退款恢复；
- 退款 reversal 同时恢复现金和被收回权益；
- Manager 正负人工调整、必填原因和负调整下限；
- 设置/Plan 变化不回写历史；
- Scheduler、Manager、无角色、Guest、User Permission、DocShare 和 API 越权；
- 直接新增、修改、删除 Credit Entry 被拒绝；
- 中途注入异常时 Payment、Consumption、Credit、Execution 整体回滚；
- 并发扣减最后 1 课时、并发付款、并发续费、并发 reversal、退款与课消并发、Plan 修改与扣减并发。

现有 M2 `+1` 测试在 M3 接入后需要创建充足的已激活测试课包作为前置夹具；只能扩充测试数据，不修改或弱化 M2 原断言。

所有写入测试和真实浏览器流程只在 `test_meixin_m1.localhost` / `127.0.0.1:18081` 执行。正式 `frontend` 仅在部署获批后 migrate，并始终只做不创建业务数据的只读验收。

## 分阶段实施与部署门禁

1. 阶段 2：创建 M3 分支，完成本设计和进度文档；建立 checkpoint 后普通 push，验证 Private 写入链路。
2. 阶段 3：新增四个 DocType schema、唯一约束、DocPerm/权限 hooks、两个服务模块骨架；只在隔离站 migrate。
3. 阶段 4：实现课包、付款、权益流水及 M2 窄联动。
4. 阶段 5：实现最小 Desk UI、Workspace 和异常提示。
5. 阶段 6：完成 M1+M2+M3 自动化、并发、权限和真实浏览器验收。
6. 阶段 7：形成 M3 candidate checkpoint。
7. 阶段 8：正式部署前创建完整备份并报告 schema、migrate、容器重建、停机、数据影响和回滚方式，等待用户确认。
8. 阶段 9：正式部署和只读验收。
9. 阶段 10：最终冻结、推进 main、push Private GitHub 并封存 M3 窗口。

Private push 若仅出现瞬时 connection reset，可在不改历史的前提下检查网络并普通重试。若出现 403、permission denied、repository not found 或 authentication failed，立即停止，不改仓库可见性、不换仓库、不重新 init、不 force push。
