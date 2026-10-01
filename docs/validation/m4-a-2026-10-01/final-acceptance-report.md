<!-- 公开副本：移除本机凭据定位，纠正第一次辅助查询错误的证据位置。原始日志/JSON以gzip无损归档，解压后可按原SHA-256验证；原始报告及证据仍保留本地outputs。 -->

# M4-A 完整 PR 最终验收报告

日期：2026-10-01，Asia/Shanghai。仓库：PENGYUQI12138/meixin-ai-admin-system。

## 1. 判断与固定范围

本轮完整覆盖 [草稿 PR #3](https://github.com/PENGYUQI12138/meixin-ai-admin-system/pull/3)，不是仅验收最后五个时序修复文件。未发现本轮必须修复的代码缺陷；源码没有变化，无新提交、推送或 CI 重跑。未合并、部署、操作正式 frontend、正式 migrate 或正式业务数据，未开发 M4-B。保留此前未清理的空目录。

- head / 本地及 origin 功能分支：`9cfb747896939fba7bda2e1d98d4d401a9bde1b0`。
- base / main：`4125002128d6c61cd92c9f11cf082fdd72946629`。
- 分支：`m4-teacher-payroll`，工作区干净，ahead/behind 0/0。
- 完整差异：19 文件、748 新增行、23 删除行；PR open、draft、mergeable=true。mergeable 只表示 Git 无合并冲突。
- 当前元数据：[GitHub 核查](M4-A-GitHub-current-2026-10-01.json.gz)、[本地 Git](M4-A-Git-local-2026-10-01.json.gz)、[完整 PR 差异](https://github.com/PENGYUQI12138/meixin-ai-admin-system/pull/3/files)。所有验收代码对应上述 head；恢复演练最后回到上述 main。

**是否可以合并：技术证据已齐，可交最终验收；业务范围仍须确认后才能建议合并。** 须明确本阶段仅用于原排课单教师实际授课，代课/共教不进入此流程，并接受暂行 15 分钟异常审核阈值。如果当前必须准确记录代课或共教，则属于当前功能归属正确性的阻塞项，不能按现有结构验收通过。PR 保持草稿，等待用户决定。

**是否可以部署：当前不具备正式部署证据。** 缺少获授权、代表正式历史的升级前数据库副本及其只读异常核查；虚构记录迁移成功不能替代该证据。还须确认正式备份/恢复、环境版本、业务范围和异常逐笔处理方案，并另行授权正式操作。

## 2. 整个 PR 的文件与功能

| 文件组 | 完整路径（仓库相对）及作用 |
| --- | --- |
| CI | `.github/workflows/ci.yml`：M4 分支 push 与目标 main 的 PR 触发 |
| 隔离运行 | `deploy/test-bootstrap.py`、`deploy/test-compose.yml`、`deploy/test-env.ps1`：独立 M4 站、资源、站点守卫和端口 |
| 设计 | `docs/M4_A_DESIGN.md`：事实口径、时序、审计、只读历史核查、后续政策 |
| 安装/权限 | `meixin_admin/hooks.py`、`meixin_admin/install.py`、`meixin_admin/permissions.py`：新 DocType 受保护、索引、禁止分享与直接改账 |
| 执行单 | `meixin_admin/meixin_admin/doctype/mx_session_execution/mx_session_execution.js`、同目录 `.py`：确认入口与同事务撤销教师分钟 |
| 新 DocType | `meixin_admin/meixin_admin/doctype/mx_teacher_hour_entry/__init__.py`、`mx_teacher_hour_entry.json`、`mx_teacher_hour_entry.py`：不可修改/删除流水 |
| 服务 | `meixin_admin/teacher_hours.py`：时长、权限、幂等、时序与反向流水 |
| 测试 | `meixin_admin/tests/browser_m4.py`、`concurrent_worker.py`、`run.py`、`test_m4.py` |
| 导航 | `meixin_admin/workspace_sidebar/美心行政.json`：教师课时入口 |

每个已提交未撤销执行单最多一笔确认；实际起止时间差按整分钟记录，净分钟为正负流水之和，不乘学生数、不以课消条数计教师时间。保存教师/课程快照、排课和执行单关联、确认人和时间。相同内容重试返回原编号；不同内容拒绝；唯一幂等键防重。已有异常历史受新校验拒绝的重试不属于正常幂等成功范围。

取消不覆盖原账，追加反向流水；修订新执行单再次完成后可有新确认。未授课为 Manager 有原因的零分钟决策，学生已到课不能确认零分钟。仅原排课的单教师，无工资、应付、支付或会计写入。

## 3. 迁移清单、升级与恢复

### 3.1 schema 与权限

新增 1 个标准 DocType `MX Teacher Hour Entry`，InnoDB、编号 `MX-THE-.#####`；既有 DocType JSON 无字段变更。

18 个业务字段：`execution`、`session`、`teacher`、`teacher_name_snapshot`、`course`、`course_name_snapshot`、`scheduled_start`、`scheduled_end`、`actual_start`、`actual_end`、`operation_type`、`effect_minutes`、`reversal_of`、`exception_reason`、`confirmed_by`、`confirmed_at`、`idempotency_key`、`demo_batch`。Link/快照/审计字段由受控服务填入；幂等键 unique；没有历史自动回填或字段默认值脚本。实际时间允许空，仅用于未授课零分钟；撤销复制原证据并写入相反分钟。

新 DocPerm：Meixin Manager / Meixin Scheduler 仅 read、select、report；无 create/write/delete/submit/cancel。保护流水的服务端 gate 和控制器禁止普通直接写、改、删；DocShare 禁止单独分享。业务角色由现有 Role fixture 提供，本 PR 不新增角色、不自动授予用户角色。

新增 2 个 after_migrate 索引：`mx_teacher_execution(execution, operation_type)`、`mx_teacher_hours_period(teacher, confirmed_at)`，另有框架生成的幂等 unique/基础索引。hooks 注册新 DocType，sidebar 同步新入口。无新增/变更 patch、fixture；原安装 hook 的既有索引和业务用户时区对齐仍会随 migrate 执行。最后时序修复增量无 schema 变更，整个 PR **有**新表和权限迁移。

### 3.2 实际升级演练

独立 Compose 项目 `meixin_m4_upgrade_test`，独立数据库/站点卷及网络，站点 `test_meixin_m1.localhost`，入口 18085。站点名沿用 main 的测试允许值，与其他 M1/正式项目无资源共用。升级源码由单独本地 clone 提供，不切换功能分支工作区。

流程：先在 main 安装 → 创建明确虚构的既有 M1/M2/M3 批次 `M4-UPGRADE-FICTIONAL-20260927` → 保存升级前备份/快照 → clone 切到 PR head → 对这份已有数据库实际 migrate → 比较。不是以新装 PR 空库替代升级。

既有样本含学生/教师/课程/教室/课包方案各 1、赠送课包 1（5 次）、已提交排课/执行单各 1、到课考勤、M2 课消 1、M3 发放和扣减流水 2，余额 4。升级前后受检记录编号、docstatus、执行单完成时间、考勤/课消规则结果、学生/课程/排课关联、权益余额均一致；新表从不存在变为存在且无历史教师课时自动生成，权限及两个索引生成。

证据：[main 基线](M4-A-upgrade-main-baseline-2026-09-27.log.gz)、[实际 migrate](M4-A-upgrade-migrate-2026-09-27.log.gz)、[升级后快照](M4-A-upgrade-after-2026-09-27.log.gz)、[表/索引/权限核查](M4-A-upgrade-history-audit-2026-09-27.log.gz)。快照覆盖上述字段，并非所有业务表逐字段校验，也不包含真实历史数据规模和特殊旧状态。

### 3.3 已执行恢复演练

2026-10-01：仅将升级 clone 切回 main，独立升级站使用升级前 `.sql.gz` 执行 `bench restore --force`，随后按 main migrate；凭据来自站外 Docker secret，未入库/报告。恢复成功，受检基线快照字节相同，余额仍 4，新 DocType 和物理表均不存在。

证据：[恢复原始日志](M4-A-upgrade-restore-2026-10-01.log.gz)、[恢复后快照](M4-A-upgrade-restored-baseline-2026-10-01.log.gz)、[哈希比较](M4-A-restore-comparison-2026-10-01.json.gz)、[物理表核查](M4-A-restored-schema-final-2026-10-01.log.gz)。升级前 DB 备份 SHA-256：`1BEB96EE4399F74E7B03EFD2E0C7FB907FC7F6889DCA09B208D0D6A744B3AB14`。备份及含敏感配置的 site_config 保留于站外私有 scratch。

两次辅助物理表查询曾因独立 Python 的 sites_path/当前目录错误而失败，第一次错误只在任务工具输出中保留，第二次错误日志为 `M4-A-restored-schema-recheck-2026-10-01.log.gz`；按已有测试脚本的 sites 工作目录重查通过。不是迁移或产品失败。恢复备份本身一次成功。

恢复方式必须是恢复升级前 DB 与匹配的代码/配置，不能仅退 Git 提交。本次虚构样本无附件，未验证正式附件恢复；实际恢复还须配套 site_config/encryption key、public/private files、框架/App 版本和停写窗口。上线后产生新教师流水时，不能直接恢复旧备份而丢弃新账，应另行制定受审查的数据恢复方案。

## 4. 验收矩阵及证据

以下代码测试、CI、普通角色业务操作对应 PR head `9cfb7478…`；升级起点/恢复终点为 main `4125002…`。仍有效的原始证据复用，不重跑无关测试。

| 要求 | 实际证据 | 结果/边界 |
| --- | --- | --- |
| 单人/多人、请假不乘人数 | `test_normal_multi_student_and_leave_count_once`；普通排课员页面 45 分钟；[当前流水快照](M4-A-role-UAT-state-2026-10-01.log.gz) | 通过，执行单 75 只生成 15 一笔 +45 |
| 全部未到课、零分钟 | `test_cancel_revision_and_zero_minutes`、`test_anomaly_and_role_gate` | 通过；须 Manager 与原因，已到课不能零分钟 |
| 同请求/不同请求及并发防重 | 普通重复断言及 `test_concurrent_same_request_returns_one_entry` | 通过；竞争者相同编号或完整回滚后重试，最终 1 笔，不保证竞争请求都立即成功 |
| actual_end 截止、等于允许 | `test_completed_at_cutoff_after_early_execution`，完成时刻精度原样比较，晚 1 秒拒绝；业务 Manager 页面超时尝试 | 通过；Manager 原因不能绕过时序 |
| 提前完成补确认 | 页面 09:09→09:29 被拒；09:09→09:28 经 Manager 原因确认为 19 分钟 | 通过，MX-THE-00016；执行单完成 09:28:57.994084 |
| completed_at 缺失 | [普通 Manager 实际 HTTP](M4-A-missing-completed-at-HTTP-2026-09-30.log.gz)，执行单 77 | 正/零请求均 417 ValidationError，期间/之后教师流水 0；仅虚构样本临时置空，finally 恢复原精确值 |
| 15/16 分钟审核 | `test_fifteen_sixteen_minute_review_boundary`；Scheduler 页面 60→45 | 差 15 可普通确认；差 16 须 Manager 原因。15 为暂行异常阈值，非薪酬舍入 |
| 时区/跨日/精度 | 站点 Asia/Chongqing；`checked_period` 拒时区感知输入；等于完成时刻测试、`test_cross_midnight_actual_period` | 通过，站点本地无时区 Datetime，用完整年月日比较，跨午夜 45 分钟；不硬编码 UTC 偏移 |
| 取消/修订/重提 | 页面取消 76→修订76-1→重新完成确认；数据库核对 16/+19、17/-19、18/+19 | 通过，净 19，保留原正负账与 revision 关联 |
| 确认与撤销竞争 | `test_confirm_cancel_race_keeps_legal_ledger`，MariaDB 行锁及共同 schedule_write 事务锁 | 两顺序通过，确认先赢 +60/-60 净0；撤销先赢无确认，状态变化不能绕过锁内校验 |
| 故障回滚/M2/M3 | `test_cancel_failure_rolls_back_teacher_m2_and_m3` | 注入 M2 失败后撤销无反向教师流水，执行单仍提交、余额2；成功撤销余额3；修订再完成余额2 |
| 普通业务权限 | Scheduler 页面成功；Manager 页面异常、取消、修订成功；DocPerm/不可变/分享 gate 测试 | 通过；沿用既有 Manager 取消、Scheduler 无取消/删除权限规则 |
| 无业务权限 | 浏览器 outsider 登录身份页→Desktop/直接流水地址均“没有权限”；[实际 HTTP](M4-A-outsider-HTTP-2026-09-30.log.gz) | 登录200 No App，读取流水403、POST确认403 PermissionError |
| 越权无业务写入 | [操作前](M4-A-role-UAT-state-2026-09-30-before.log.gz)、[越权后](M4-A-role-UAT-state-2026-09-30-after-outsider.log.gz)、[当前核对](M4-A-UAT-snapshot-comparison-2026-10-01.json.gz) | 三份受检快照 SHA-256 相同；不是整个数据库字节校验。角色 gate 在写入之前执行 |
| main→PR升级/恢复 | 第3节原始日志 | 虚构既有记录演练通过，真实历史兼容性未验证 |
| 代课/共教 | 单教师字段/服务签名及 `test_substitute_and_coteaching_are_not_silently_recorded` | 不支持，无实际代课/共教验收通过声明；该测试仅证明函数无替代教师参数且原教师归属/无共教字段 |
| 无工资记录 | 完整 PR 无工资 DocType/写接口 | 符合范围；未来结算接口仅设计要求 |

浏览器操作由实际工具在隔离入口 18084 执行；页面 DOM/截图保留于本任务工具记录，未另行导出截图文件。可独立下载的原始证据是回归、HTTP 与数据库日志，不把数据库快照说成浏览器录屏。

### 回归与 GitHub CI

[完整 Frappe 原始回归](M4-A_Frappe_复核后原始回归_2026-09-27.log.gz)：M1 30、M2 22、M3 89、M4 11，共 **152/152，0 失败、0 错误、0 跳过**。SHA-256 `119A3594FD425DC930A7E450AB3E198EA3717854B8887031AE90656E3E784487`。本轮源码未变，测试数仍152，新增验收操作不计作新增单测。

- [分支 CI](https://github.com/PENGYUQI12138/meixin-ai-admin-system/actions/runs/36284781957)：push，head 9cfb7478…，completed/success。
- [PR CI](https://github.com/PENGYUQI12138/meixin-ai-admin-system/actions/runs/36284893733)：pull_request，PR head 9cfb7478…，completed/success。前轮实际 checkout 日志核对合并模拟提交 `956e298f17b1a0ab22f470f4e2f272ceb687723a`、目标 main 4125002…。当前 API 再查仍成功，无本轮新 run。
- 配置覆盖功能分支 push、目标 main 的 PR。CI 检查差异空白、Python/JSON/高置信凭据模式、全仓 JS 语法及 Frappe-free 单测；**不运行152项数据库集成回归、升级恢复或角色浏览器操作**。这些证据来自独立本地站。两次实际 CI 没有跳过/待审批（前轮逐步核查），不扩大其覆盖声明。

## 5. 页面与数据库逐笔一致性

| 执行单 | 状态 | 教师流水 | 事实 |
| --- | --- | --- | --- |
| MX-EXE-00075 / session93 | submitted | MX-THE-00015 +45 | Scheduler，实际06:33→07:18，完成09:28:57.694908 |
| MX-EXE-00076 / session94 | cancelled | MX-THE-00016 +19、00017 −19 | Manager，实际09:09→09:28；00017.reversal_of=00016 |
| MX-EXE-00076-1 / session94 | submitted，amended_from=00076 | MX-THE-00018 +19 | Manager，再次完成09:57:46.405188，新幂等键 |

session93净45、session94净19。原执行单保留完成时间和取消状态；新执行单重新完成。M2对应流水计数分别2、2、1，课消规则与教师分钟分开。此角色 UAT 不配置课包/权益，相关学生 M3 流水为0；不能用该浏览器流程证明 M3 扣减正确性。M3余额证据来自完整回归和升级批次（余额4）。当前读取与09-30越权前后快照完全一致，没有重复创建15–18流水。

## 6. 历史只读核查及真实证据缺口

数据全部虚构，无正式数据库副本。使用设计文档两条只读 SQL，检查已提交执行单缺 completed_at、正分钟确认原单/实际结束缺失或 actual_end>completed_at，并关联受影响课时。

| 隔离来源/时间 | 执行单/教师流水 | 缺完成时间 / 非法正分钟确认 / 受影响课时 |
| --- | --- | --- |
| 升级站，09-27升级后 | 1 / 0 | 0 / 0 / 0 |
| M4站，09-27角色UAT前 | 0 / 0 | 0 / 0 / 0（空状态不能验证历史兼容） |
| M4站，09-30及10-01角色UAT后 | 4 / 4 | 0 / 0 / 0 |

证据：[升级站只读日志](M4-A-upgrade-history-audit-2026-09-27.log.gz)、[早期M4只读日志](M4-A-existing-isolated-history-audit-2026-09-27.log.gz)、[当前M4只读日志](M4-A-history-audit-2026-10-01.log.gz)。缺时间拒绝用例为单独临时异常，测试结束恢复其原精确完成值，因此不出现在最终异常数量中。

结论只证明查询和约束在上述虚构样本的表现，**不能判断正式历史是否有异常**。还缺目标 main 对应的代表性正式历史副本、采集时点/来源/范围及合规授权，及副本升级后的异常明细/数量/逐笔影响、余额/关系/权限核对和恢复演练。没有导入或查询正式数据。

新逻辑不自动纠正旧异常：旧流水仍可读并参与现有汇总；缺完成时间或超时确认的同内容重试会在查旧流水前被拒；管理员原单取消仍会追加反向账。应逐笔查考勤、原单、审批证据，按取消→修订→重新确认处理；不猜测或批量回填 completed_at。只读核查完成前，不能把零异常测试结果当正式历史清白证明。

## 7. 业务范围、M4-B接口与政策

**当前正确性边界：**只记录原排课单教师。代课不能替换教师归属，共教不能拆分人员或时间；现有测试不证明 API 能识别真实授课者。不得用本功能对代课/共教直接确认后宣称归属正确。若这些是本次上线必须覆盖的实际业务，验收阻塞；若用户明确从 M4-A 使用范围排除，则列后续需求。无需在本轮自行发明分配政策或开发工资。

**未来已结算撤销接口要求（未实现）：**以教师课时流水作为事实源，结算行保存原确认ID/执行单/教师/课程/分钟与政策版本；取消使用 reversal_of 关联，识别已结算部分并追加差额调整，关联原结算行和期间，保留审批、原因、创建人/时间、唯一幂等键。不得删除/覆盖已结算工资，不能因为旧期间封账而阻止事实取消；事实账与结算处理应有可审计的待调整状态、事务/重试规则。工资或支付对账、调整入账期间与权限须另定，本轮不建立这些实体。

待定：课时计薪单位/舍入、费率、生效版本、无人到课但待命是否计薪、代课/共教、节假日/跨日、结算周期/审批权限、已结算撤销差额处理。它们不妨碍按已确认单教师范围记事实；15分钟审核阈值须业务接受，不能直接用于工资舍入。

## 8. 最短用户最终验收与后续事项

无需重做全部测试。独立 M4站当前已启动，升级站保留恢复后的 main 状态；本地接口仅测试端口，凭据在站外私人 scratch，不写入报告/PR。

1. 打开 `http://localhost:18084`，用虚构 Scheduler 登录；查执行单 `MX-EXE-00075`及课时`MX-THE-00015`，预期原教师、课程关联正确，仅+45分钟。虚构测试账号凭据单独在本机保管，不公开。
2. 用虚构 Manager 查`MX-EXE-00076`及修订`MX-EXE-00076-1`；预期旧单已撤销，新单已完成，课时16/+19、17/−19、18/+19，session94净19，不再点击提交创建新单。
3. 用无角色虚构账号登录，访问 Desktop 或 `/desk/mx-teacher-hour-entry/MX-THE-00018`；预期“没有权限”。直接接口403与缺完成时间417已有原始日志，不需要用户修改数据库复演。
4. 作最终决定：是否接受本阶段原排课单教师范围（排除代课/共教）及15分钟暂行审核阈值。确认后再决定PR转正式审查/合并，当前不操作。
5. 若计划部署，先另行提供获授权的正式历史副本，在独立环境按main→PR演练、只读核查并逐笔讨论异常；准备匹配DB/配置/附件/版本的可恢复备份，另行授权上线。

这是合并前验收快照；用户后续已批准口径及合并，最新封存状态见 ../../M4_A_ACCEPTANCE.md。证据文件及SHA-256见同目录 evidence-index.json；敏感备份/配置/凭据未入库。
