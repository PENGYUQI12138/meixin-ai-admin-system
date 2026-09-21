# 美心行政 M1 / M2 / M3 开发进度

最后更新：2026-09-21（Asia/Shanghai）

## 恢复工作规则

每次恢复先执行：

```powershell
Get-Content PROGRESS.md
git status --short --branch
git log --oneline -5
```

只从“下一步操作”继续。每完成一个独立阶段，立即更新本文件；重要节点执行 Git commit。不得把未运行的检查记为通过，不得在 `frontend` 运行测试套件。

## M3 当前状态

### 阶段 2：设计与 Private Git 写入验证（已完成）

- M2 final 已完成、正式部署并冻结；本地 `main`、`origin/main` 和 GitHub `main` 均为 `615316ee991a172f198a378b2fa33db2ae60b000`。
- GitHub 仓库已转为 Private；阶段开始前 `git ls-remote origin refs/heads/main` 成功，Private 读取链路正常。
- 已从 M2 final 创建独立分支 `m3-enrollment-payment-package`，不 rebase、reset、amend、force push 或改写 M1/M2 历史。
- M3 采用 `MX Package Plan`、`MX Student Package`、`MX Payment`、`MX Lesson Credit Entry` 四个核心 DocType，以及 `entitlements.py`、`payments.py` 两个服务模块。
- M3 严格分离现金、课时权益和 M2 课消事实；采用满款一次性授予、退款关闭课包、禁止负余额、精确 Course、FEFO/FIFO、多层幂等和同事务 M2 联动。
- 完整设计见 `docs/M3_DESIGN.md`。第一个 M3 checkpoint 为 `8d6f346`，只包含设计和状态文档，没有 schema、迁移、容器或正式站业务数据修改。
- 已普通执行 `git push -u origin m3-enrollment-payment-package`；Private GitHub 首次真实写入成功，远端同名分支已建立并正常跟踪，没有 connection reset 或鉴权错误。

### 阶段 3：Schema、唯一约束与权限骨架（已完成）

- 新增 `MX Package Plan`、`MX Student Package`、`MX Payment`、`MX Lesson Credit Entry` 四个 DocType，以及 `entitlements.py`、`payments.py` 两个最小服务模块骨架。
- Package Plan 仅 Manager 可维护；Scheduler 可创建普通 Student Package 和普通收款，但不能创建赠送包、退款关闭或撤销；Credit Entry 对两个业务角色均只读。
- Student Package 和 Payment 的 `request_id`、Credit Entry 的 `idempotency_key`、非空 `m2_consumption_entry`、非空 `reversal_of`，以及 Payment 非空 `reversal_of` 均已通过 DocType `unique` 建立 MariaDB 唯一索引。
- 系统 request ID 为隐藏只读字段并由服务器生成；课包快照由服务器从 Package Plan 生成；Payment 的学生、币种和 `cash_effect` 由服务器推导，不信任前端输入。
- 新 DocType 已加入业务角色 gate、permission query hooks 和禁止 DocShare 集合；Credit Entry 控制器拒绝任何直接新增、普通修改和删除。
- Python 编译、全部 JSON 解析和 `git diff --check` 通过。仅对隔离站 `test_meixin_m1.localhost` migrate，成功同步四个 DocType、索引、DocPerm 和 `after_migrate`；正式 `frontend` 未 migrate、未重建、未写入。
- 隔离 MariaDB 已实际核对全部唯一索引和四个 DocType 的 DocPerm，不只依赖源码声明。
- 新增 4 项 M3 schema 骨架测试，覆盖产品/购买快照、系统 request ID、Scheduler 越权拒绝、服务器现金效果和 Credit Entry 直接写入拒绝。完整结果为 `Ran 56 tests in 15.535s / OK`：M1 30/30、M2 22/22、M3 4/4。

### 阶段 4A：Student Package、普通付款与满款激活（已完成）

- 购买型 Student Package 提交后不授予权益；普通收款支持一次付清和分次付款，锁内按 Decimal 重新汇总已提交 `cash_effect`，拒绝超额和负净付款。
- 净收款第一次恰好达到成交金额时，通过唯一 `package-grant:<student-package>` 幂等键生成完整 `+N` Credit Entry，并只写一次 `activated_at`；分次付款、双击和 API 重试不会重复 grant。
- 赠送包继续只允许 Manager，提交时在同一事务直接生成唯一权益；阶段 4D 尚未实现的付款撤销和退款关闭在 `before_submit` 明确拒绝，避免提前暴露不完整资金流程。
- 新增受权限保护的 `record_payment` 幂等 API；相同 request ID 和相同内容返回/完成原 Payment，不同内容拒绝。Student Package、Payment、Credit Entry 均继续复用现有 `schedule_write`，没有手动 commit 或新锁服务。
- 首次并发测试发现锁后普通 SUM 仍读取 MariaDB REPEATABLE READ 旧快照，导致两笔付款完成但未 grant。汇总已改为 locking/current read；随后确认等待前已读取草稿的第二个标准 Document 请求会按 M1/M2 既有策略整体回滚并提示 fresh retry，重试后两笔付款完整、grant 仅一条。
- 新增 7 项 4A 测试，覆盖待付款、Scheduler 一次付清、分次付款、超额拒绝、API/double-click 幂等、赠送包和并发付款安全重试。最终完整结果为 `Ran 63 tests in 16.932s / OK`：M1 30/30、M2 22/22、M3 11/11。
- 测试后隔离站 `TEST-M3-%` 的 Package Plan、Student Package、Payment、Credit Entry、Student 和 Course 均为 0。Python 编译和 `git diff --check` 通过；正式 `frontend` 未 migrate、未重建、未写入。

### 阶段 4B：M2 与课时权益同事务联动（已完成）

- 仅在冻结的 `consumption.py` 增加窄调用点：M2 `+1` 决定同步调用权益服务生成唯一 `-1`，`0` 明确不动作，M2 `-1` reversal 同步对原扣减生成唯一 `+1`。
- 扣减使用 `m2-consume:<M2 Consumption Entry>`，返还使用 `m2-restore:<M2 reversal Consumption Entry>`；应用层核对相同键内容，MariaDB 的 `idempotency_key`、非空 `m2_consumption_entry` 和非空 `reversal_of` 唯一索引继续作为最终防线。
- reversal 固定返还原 `-1` Credit Entry 所属 Student Package，不重新选包、不覆盖旧流水。所有调用沿用原 `schedule_write` 和 Frappe 请求事务，没有手动 commit、异步任务或新锁服务。
- 4B 先实现单一合法候选的安全扣减；没有候选或存在多个候选均明确拒绝并整体回滚，不猜测课包。完整 FEFO/FIFO、多原因分类和多课包分配保留到 4C。
- M2 22 项旧测试只扩充已激活赠送课包夹具，未删除或弱化原断言；并发双提交测试改为真实 `+1`，确认只有一条 M2 决定及一条 M3 扣减。
- 新增 9 项 4B 测试，覆盖 `+1/0/-1`、决定和 reversal 重试幂等、无合法包、Credit 唯一冲突、reversal 中途失败与 fresh retry。故障注入确认 Execution、M2 和 M3 均不留半成品。
- 最终完整隔离回归结果为 `Ran 72 tests in 19.271s / OK`：M1 30/30、M2 22/22、M3 20/20。测试后七类 `TEST-%` 业务表均为 0；隔离 MariaDB 再次确认 Credit Entry 三个业务唯一索引存在。
- Python 编译、全部 JSON 解析和 `git diff --check` 通过；正式 `frontend` 未 migrate、未重建、未运行测试、未写入。

## M2 当前状态

### 阶段 1：设计与分支基线（已完成）

- M1 冻结基线、本地 `origin/main` 与 GitHub `origin/main` 均为 `29d622e3e2b946170aa36fe51fbebeb5f2bf3719`，开始前工作区干净。
- 已从该提交创建独立分支 `m2-attendance-consumption`；不改写、rebase、reset、amend 或 force push M1 历史。
- M2 采用 `MX Session → MX Session Execution → MX Session Attendance → MX Lesson Consumption Entry` 分层，不在已提交排课中加入考勤字段。
- 设计采用五项三态课消规则、不可变且数据库幂等的 `+1/0/-1` 流水、Manager 专属纠错、Scheduler 正常时间提交、Manager 有原因提前完成。
- 完整设计见 `docs/M2_DESIGN.md`。本阶段没有迁移数据库、修改容器或触碰正式站点业务数据。

### 阶段 2：M2 Schema、规则字段与权限（已完成）

- 新增可提交 `MX Session Execution`、子表 `MX Session Attendance`、只读 `MX Lesson Consumption Entry` 三个 DocType。
- `MX Settings` 新增到课、请假、缺勤、其他、课程取消五项三态规则，默认均为“未配置”。
- Manager 可提交/取消/修订执行单；Scheduler 可创建、保存和提交，但无取消、修订或删除权限；两者对课消流水仅有读取/报表权限。
- `idempotency_key` 已通过 DocType `unique` 在 MariaDB 建立唯一 BTREE；新增执行、考勤和流水查询索引。
- Python 编译、全部 JSON 解析和 `git diff --check` 通过。
- 仅在隔离站 `test_meixin_m1.localhost` 执行 migrate，成功同步三个 DocType、五项规则、权限、唯一约束和索引；正式 `frontend` 未迁移、未重建、未写入。

### 阶段 3：执行、考勤冻结与不可变课消流水（已完成）

- 执行单提交在 M1 MariaDB 站点级写锁内复核原排课、完整名单、唯一有效执行和计划结束时间；Scheduler 只能正常时间完成，Manager 提前完成必须保存原因、完成人和完成时间。
- 五项规则修改也复用同一写锁；提交先解析全部涉及规则，再冻结到考勤行并生成每名学生的 `+1` 或 `0` 决策流水，设置后续变化不改写历史。
- 排课取消按“课程取消”规则为原名单生成流水；存在执行草稿或已完成执行时禁止绕过处理直接取消。
- 执行取消为全部原决定追加 reversal：`+1 → -1`、`0 → 0` 撤销记录；修订只允许 Manager 从最近的已取消执行单产生新链。
- Manager 可对仍有效的 `+1` 人工撤销，必须填写原因；幂等重试返回同一 reversal，内容不同则拒绝。
- 课消流水控制器禁止直接新增、更新和删除；所有写入都经内部业务上下文，旧流水永不覆盖。
- 新增 17 项 M2 集成测试，覆盖未配置规则、混合考勤、0 流水、提前完成、名单、单一有效执行、自动/人工撤销、修订、课程取消、不可变、历史冻结、失败回滚及三项并发。
- 首次 46 项回归为 M1 30 项全通过、M2 14 通过、1 失败、1 错误；两项均为测试断言问题（删除目标未查询 `name`、期望异常类型不符），业务结果符合预期。修正并补充规则并发测试后再次隔离 migrate，最终完整结果为 `Ran 47 tests in 14.339s / OK`。
- 测试后隔离站 `TEST-M2-*` 主数据、执行单和流水均为 0，五项规则恢复“未配置”。正式六个应用容器仍使用 M1 final 镜像，未迁移、未重建、未写入。

### 阶段 4：最小 Desk 流程与 Workspace 入口（已完成）

- 已在确认排课表单显示派生执行状态，并提供“记录上课结果 / 查看执行单”入口；新建执行单由服务器按冻结排课名单填充考勤行。
- 已为执行单提供原排课、课消流水入口，为 Manager 在 `+1` 决策流水上提供填写原因后追加人工撤销的入口；流水表单继续保持不可编辑、不可删除。
- Workspace 新增“上课执行”和“课消流水”入口，未改变 M1 排课字段、冲突检测、周课表或历史保护逻辑。
- UI API 集成测试发现并修复两项真实问题：新文档返回浏览器前补充 `__islocal`；草稿允许暂未填写考勤状态，但提交时仍由服务器强制完整校验。
- 仅在隔离站再次 migrate；Workspace 元数据和流水 DocPerm 核对通过，三个相关 JavaScript 文件 `node --check` 通过。
- M1 30 项与 M2 18 项完整隔离回归结果为 `Ran 48 tests in 14.336s / OK`。正式 `frontend` 未迁移、未重建、未写入。
- 真实浏览器自动化通道连续返回 `nodeRepl.fetch request failed`，因此本阶段只完成源码、元数据、API 与静态验证；浏览器点击和控制台验收保留在阶段 5，未记为通过。

### 阶段 5：回归、并发、权限与真实浏览器验收（已完成）

- 按正确性、简洁性、架构、安全和性能五个方向审查 M2 全部差异；未新增依赖或第二套锁服务。
- 审查发现“先人工撤销单名学生，再取消整张执行单”会被已有 reversal 阻断。现改为在同一写锁内复用已有 reversal，仅对尚未反转的决定追加撤销，禁止重复 `-1`。
- 新增测试覆盖上述组合路径、Scheduler 修改规则/取消执行/人工撤销的越权拒绝、幂等键相同内容重试与不同内容冲突、Guest/外部用户及 User Permission 隔离。
- 首次新增权限测试运行中，51 项通过、1 项因测试仍停留在外部用户身份而无法创建权限夹具；切回 Administrator 创建夹具后完整结果为 `Ran 52 tests in 14.670s / OK`（M1 30/30、M2 22/22）。
- 测试后隔离站 `TEST-M2-%` 执行单和流水均为 0，五项规则均恢复“未配置”；数据库确认 `idempotency_key` 为唯一 BTREE。
- 正式六个应用容器仍运行 `meixin-admin:m1-final-frappe16.34.0`，未 migrate、未重建、未写入。
- 只读确认隔离主页、登录页、`/api/method/ping` 和 Socket.IO 轮询均为 HTTP 200，四个隔离容器运行、MariaDB healthy、最近日志无应用错误；原故障判定为浏览器控制工具链问题，不是 Frappe、站点或容器故障。
- 重置浏览器控制会话并新建 IAB 标签后恢复控制；使用独立 Cookie 域登录专用 Scheduler 验收账号，Manager 使用原隔离会话，全程未触碰正式站点。
- 真实点击通过 Workspace 新入口、确认排课进入执行、四种考勤、“其他”说明必填、未配置规则阻止、配置后 Scheduler 正常提交、`+1/0` 流水、流水只读、Scheduler 无取消/修订/人工 reversal 入口、Scheduler 提前完成拒绝、Manager 有原因提前完成。
- Manager 先人工撤销一条 `+1`，再取消整张执行单：4 条决定最终对应 4 条 reversal，总流水从 4 条变为 8 条；已有人工 `-1` 未重复生成，其他 `+1` 产生 `-1`，原 `0` 产生带来源的 `0` reversal；修订按钮成功进入带 `amended_from` 的新草稿。
- 全新浏览器标签逐页巡检 Workspace、执行列表/详情、流水列表/详情，最终控制台实际为 `0 error / 0 warn`，无明显显示异常。
- 验收后删除本轮 `BROWSER-M2-20260921-1355` 排课、执行、考勤、流水和档案夹具及专用 Scheduler 账号；七类批次数据均为 0，五项规则恢复“未配置”，隔离 ping 仍为 200。

### 阶段 6：M2 candidate checkpoint（已完成）

- 阶段 1～5 的设计、Schema、业务实现、最小 UI、52/52 自动化、并发/权限审查和真实浏览器验收均已完成。
- 本阶段只更新验收与进度文档，不修改已通过测试的业务代码；本提交作为正式部署前的 M2 candidate checkpoint。
- 正式六个应用容器仍运行 `meixin-admin:m1-final-frappe16.34.0`；正式 `frontend` 未执行 M2 migrate、容器重建、测试或业务写入。

### 阶段 7：正式部署与冻结验收（已完成）

- 以 candidate `603807a` 构建 `meixin-admin:m2-final-frappe16.34.0`，镜像 ID `sha256:35f61720d2d17494011b4d0bb23fcfa15fdc0704f37cc756191da32780a0b30b`；镜像内 80 个 Git 跟踪文件与 candidate 逐文件 SHA256 一致，Frappe 16.34.0、ERPNext 16.35.0 未升级。
- 正式 M2 schema 变更前创建 Git 外完整备份 `D:\FrappeProject\backups\meixin-m2-predeploy-20260921_142856`；数据库 gzip、public/private tar、站点配置及环境 JSON 均实际读取通过，SHA256 清单复核一致。
- 仅用 `--no-deps` 重建 frontend、backend、websocket、queue-short、queue-long、scheduler 六个应用容器；MariaDB、两个 Redis 容器及 sites、logs、数据库和 Redis 持久化卷未重建、删除或恢复。
- 2026-09-21 约 14:33（Asia/Shanghai）仅执行一次 `bench --site frontend migrate`，随后执行一次 `clear-cache`，退出码均为 0；没有运行 install-app、configurator、create-site、正式测试套件或演示初始化。
- migrate 同步三个 M2 DocType、五项规则字段、权限、Workspace 和 `idempotency_key` 唯一 BTREE。正式既有 `MX Settings` Single 对新字段没有持久化默认值，浏览器初次显示空白；经用户明确授权，2026-09-21 18:50 通过正常 Document `set/save` 生命周期仅把五个空值规范化为“未配置”，没有覆盖非空值或修改其他设置。
- 最终真实浏览器逐项通过 Workspace、M1 七个入口、M2 两个入口、Execution 未保存表单内 Attendance 子表、Consumption Entry 列表和五项规则显示；全新标签控制台为 `0 error / 0 warn`。
- 六个应用容器均运行同一 M2 final 镜像且重启数为 0；MariaDB healthy、两个 Redis PONG，主页、ping 和 Socket.IO 均 HTTP 200，Administrator 与业务用户均为 `Asia/Chongqing`。
- 正式部署前后 MX Student、Teacher、Course、Room、Session、Session Student、Execution、Attendance 和 Consumption Entry 均为 0；正式站没有创建任何测试业务记录。
- 隔离自动化结果继续保持 M1 30/30、M2 22/22、合计 52/52；正式站按边界未运行测试套件。

## 已完成内容

### 阶段 1：现场核查与修改前备份（已确认）

- 已读原 `frappe_docker` README、CONTRIBUTING 和自定义 App 文档；任务目录及父目录未发现 AGENTS.md。
- 已确认 Windows + Docker Compose；唯一站点 `frontend` 经用户确认属于开发/试点站点。
- 已确认 Frappe 16.34.0、ERPNext 16.35.0、MariaDB 11.8.9、站点时区 `Asia/Chongqing`。
- 修改前仅安装 frappe、erpnext；没有 Custom DocType 或 `MX` DocType；原仓库无未提交改动。
- 已执行数据库、公有文件、私有文件和站点配置备份，副本位于 Git 外的 `D:\FrappeProject\backups\meixin-m1-20260920`，已计算 SHA256。
- 原服务已恢复运行；没有升级框架、重装站点、删除卷或覆盖数据。

### 阶段 2：M1 App 源码与原生界面（源码完成，静态检查通过）

- 创建独立 App `meixin_admin`、模块 `Meixin Admin`。
- 实现 7 个 DocType、两类业务角色、中文 Workspace、Frappe v16 Desktop Icon / Workspace Sidebar、翻译、表单/列表/原生周历。
- 实现服务端排课校验、提交冲突检查、取消/修订约束、档案停用与容量保护。
- 实现 MariaDB 事务级粗粒度排课写锁；MariaDB 11.8 锁冲突时全事务回滚并给出中文重试提示。
- 实现权限 gate、权限查询条件、禁止临时分享、日历 API 字段白名单和隐私保护。
- 实现可重复演示初始化和只读清理预览；安装/迁移不会自动灌数据或创建账号。
- 完成中文 README、环境、架构、下一步、隔离测试文档，以及固定原镜像 digest 的 Dockerfile。

### 阶段 3：独立测试环境与服务器集成测试（已确认可用）

- 创建独立 Compose 项目 `meixin_m1_test`，使用独立 MariaDB/Redis/sites 卷和 `127.0.0.1:18081`。
- 创建严格命名的测试站点 `test_meixin_m1.localhost`，配置 `meixin_test_site=1`、`allow_tests=1`。
- App editable 安装、`install-app`、`migrate` 成功；DocType、Workspace Sidebar、Desktop Icon 同步成功。
- 登录页已通过 HTTP 和浏览器加载；未复制真实站点数据。
- 2026-09-21 修复 Frappe 16 权限检查兼容调用后，严格隔离站点完整运行 27 项：27 通过、0 失败、0 错误、0 跳过。
- 通过项覆盖正常排课、教师/教室/学生冲突、相邻时段、无效/缺失起止时间、重复学生、超容量、禁用档案、跨午夜、取消释放、提交后禁改、修订重检、业务角色与无角色拒绝、日历筛选、冲突隐私、演示幂等/失败原子性、并发冲突提交、并发停用与并发缩容。

### 阶段 4：独立测试站点界面验收（已确认可用）

- 已在真实 Desk 界面走通工作台、建档、排课草稿、提交确认、冲突拒绝和周课表筛选。
- 已确认中文冲突提示包含冲突对象、既有排课编号和时段，失败记录保持草稿。
- 已确认周课表用绿色区分确认课、橙色区分“不占用”的草稿，取消记录默认隐藏，并显示实际站点时区 `Asia/Chongqing`。
- 已将隔离 Web 入口改为 Nginx 同源代理，修复 `/socket.io` 连接；全新课表页控制台 0 error、0 warn。
- 已去除隔离 App 容器重建时下载 Python 构建依赖的要求，源码通过路径文件加载，离线重启成功。
- 关键界面截图已保留在当前 Codex 任务记录中；隔离运行数据和截图不提交 Git。

### 阶段 5：最终复核（已完成）

- 按功能范围、权限与隐私、事务与并发、Frappe 16/部署兼容、运维文档五个方向复核源码和实际结果。
- 修复“取消后仍可删除历史排课”：已提交或已取消排课现在由服务器禁止删除，管理员仍可删除草稿；补充回归断言。
- 将隔离 Nginx 入口改为启动前一次性生成配置，避免重启时读取旧配置的竞争；容器强制重建和二次重启均验证成功。
- 增加 `.gitattributes`，确保 Windows Git 检出后测试入口 shell 脚本仍使用 LF。
- 最终静态检查、Compose 配置校验、秘密扫描通过；完整 27 项再次通过；重启后的全新课表页控制台仍为 0 error、0 warn。

### 阶段 6：开发/试点站点安装与服务冒烟（已完成）

- 用户明确确认容器切换后，构建固定原版本 digest 的派生镜像，并仅用 `--no-deps` 重建六个应用容器。
- 数据库、Redis、站点、日志及其他持久化卷未重建或删除；未运行 configurator/create-site，未升级 Frappe/ERPNext。
- `frontend` 已成功安装 `meixin_admin 0.1.0`；`install-app`、`migrate`、`clear-cache` 均以退出码 0 完成。
- 所有相关容器已恢复运行，数据库健康；首页、登录页 HTTP 200，Socket.IO 握手有效。
- 已只读确认 7 个 DocType、两个业务角色、Workspace/Sidebar/Desktop Icon、机构默认值和 `Asia/Chongqing` 时区。
- Guest 访问美心上下文及日历 API 均为 HTTP 403；最近应用日志无 Traceback/ERROR/CRITICAL/ModuleNotFoundError。
- 未运行演示初始化、未创建账号、未在 `frontend` 运行测试套件。

### 阶段 7：关机中断恢复与正式站点可视验收（已完成）

- Windows 正常关机后先核对 Git、镜像、容器、卷、站点和运行配置，没有重复安装、迁移或测试。
- 六个应用容器均运行最终镜像 `meixin-admin:m1-frappe16.34.0`，镜像 ID 为 `sha256:a56341d2de5321e8cc6d9944a9e9e4d53fb3cad77bb9e43e4c317fd8bd143c9c`；MariaDB 健康，Redis、`sites`、`logs`、`db-data` 等持久化卷存在并正常挂载。
- 正式入口 Nginx 的 `/socket.io` 使用内部可达的 `Origin: http://frontend:8080` 与 `Host: frontend:8080`；隔离入口使用 `http://app:18081` / `app:18081`。两套入口均保持浏览器同源访问，解决独立 WebSocket 容器无法通过 `localhost:8080` 回调认证的问题。
- `http://localhost:8080` 返回 HTTP 200；正式站点全新浏览器页不再出现 `Invalid origin`、`fetch failed`、error 或 warn。
- 已只读走通正式“美心行政”工作台、学生/教师/课程/教室新建空表单、排课新建空表单、机构设置和周课表；字段、草稿提示、图例、筛选和 `Asia/Chongqing` 时区显示正确。
- 正式站点未保存任何业务记录；隔离环境重启后再次打开周课表，既有虚构数据正常显示且控制台 0 error、0 warn。

### 阶段 8：最终人工验收整改状态审计（已完成，整改进行中）

- 整改开始前 Git 工作区干净，`HEAD` 为稳定提交 `4ec6948`；相对该提交没有 Workspace 误判遗留修改。
- Edge 先前 Workspace 入口缺失由登录账号不同造成；Administrator 下工作台及入口完整，不放宽 Workspace 或业务权限。
- 隔离站点时区为 `Asia/Chongqing`，但 Administrator 用户时区为 `Asia/Kolkata`；正式站点两者均为 `Asia/Chongqing`。本轮通过 Frappe 站点/用户时区配置对齐，不做固定小时偏移。
- 正式站点当前无学生档案；隔离站点有 5 条人工测试学生，其中 4 条监护人电话为空。监护人电话将对新建和后续编辑设为必填，不伪造回填、不删除既有记录。
- 本轮限定修改学生/课程档案字段、排课默认结束时间、日历默认滚动位置、隔离时区初始化、演示/测试与文档；不改冲突算法、并发锁、排课数据模型或 Workspace 权限。

### 阶段 9：最终人工验收源码整改（已完成，待隔离验证）

- 学生增加可选“就读学校”；年级改为覆盖小学、初中、高中的标准 Select；监护人电话对新建及后续编辑设为必填，既有空值不伪造回填。
- 课程学科改为标准 Select，课程名称和默认时长保持不变。
- 排课表单按课程默认时长自动填写尚未人工指定的结束时间；服务器也只在结束时间为空时补默认值，人工结束时间保持不变。
- 继续由原服务器逻辑拒绝空学生名单并执行冲突、容量和启用状态校验；未改写排课锁与冲突算法。
- 周课表保留全天数据和滚动能力，进入周/日时间网格时默认定位 08:00。
- 安装后将 Administrator 及已分配美心业务角色的用户时区对齐 Frappe 站点时区；隔离初始化同时显式设置 Administrator 为 `Asia/Chongqing`，不使用固定小时偏移。
- 已新增字段标准化、电话必填、默认结束时间、人工结束时间保留和业务用户时区回归用例；Python 编译、JSON 解析、相关 JavaScript `node --check`、`git diff --check` 通过。
- 隔离迁移与浏览器配置首次执行成功；首次 30 项回归为 29 通过、1 失败。唯一失败是旧用例仍要求空结束时间被拒绝，与新需求“按课程默认时长补齐空结束时间”冲突；其余 29 项通过，正在只修正该过时断言后重跑完整套件。

### 阶段 10：最终人工验收隔离回归（已完成）

- 修正过时断言后完整 30 项运行：`Ran 30 tests in 8.008s / OK`，0 失败、0 错误、0 跳过。
- 原有正常提交、教师/教室/学生冲突、相邻时段、容量、跨午夜、取消/修订、权限、失败回滚及三项并发测试均未退化。
- 新增回归覆盖就读学校、年级/学科标准选项、电话必填、默认时长补结束时间、人工结束时间保留，以及 Administrator/美心教务用户时区对齐。
- 隔离站点实际确认站点与 Administrator 均为 `Asia/Chongqing`；学生、课程新建表单及机构设置显示正确。
- 浏览器实际验证 60 分钟课程把 10:00 自动补为 11:00；手动改为 11:30 后再改开始时间为 10:15，结束时间仍为 11:30。
- 周课表默认视口显示 08:00 附近且仍可上下滚动查看全天；最终全新排课表单、机构设置和周课表控制台均为 0 error、0 warn。
- 浏览器整改验收未保存任何记录；正式 `frontend` 尚未迁移、重建或切换镜像。

### 阶段 11：正式站点备份、部署与只读验收（已完成）

- 用户确认部署边界后，执行正式站点数据库、公有文件、私有文件和站点配置完整备份；Git 外副本位于 `D:\FrappeProject\backups\meixin-m1-final-20260921_095041`。
- 已完整读取数据库 gzip、打开两个 tar、解析站点配置 JSON，并生成 `SHA256SUMS.txt`；未输出站点配置内容。
- 构建独立标签 `meixin-admin:m1-final-frappe16.34.0`，镜像 ID `sha256:b5dcea32c81713473225e8d40e5e2cf25c9f6a98b911af245cd94e1650feab97`；基础版本仍为 Frappe 16.34.0、ERPNext 16.35.0。
- 仅用 `--no-deps` 重建 frontend、backend、websocket、queue-short、queue-long、scheduler 六个应用容器；数据库与两个 Redis 容器 ID 未改变，sites、logs、数据库及 Redis 持久化卷未删除或重建。
- 本轮只执行一次 `bench --site frontend migrate`，成功完成 DocType 同步及 `after_migrate`；随后执行一次 `clear-cache`。没有运行 configurator、install-app、create-site、演示初始化或正式站点测试套件。
- 正式元数据确认学校字段、标准年级、监护人电话必填和标准学科已经生效；站点及 Administrator 用户时区均为 `Asia/Chongqing`。部署前后正式站点 M1 业务记录均为 0 条。
- 正式全新浏览器页确认 Workspace 七个入口、学生/课程/排课表单、机构设置与周课表正常；周课表保留全天并默认滚动到约 08:00；控制台 0 error、0 warn，最近 60 秒 frontend/websocket 日志无错误匹配。
- 正式站点没有课程等业务档案，因此遵守“不创建业务测试数据”要求，未在正式站重新演示课程驱动的自动结束时间；该交互已在隔离站真实表单验证，正式站已确认同一最终镜像及排课脚本加载。

## 修改文件

- App 元数据与钩子：`pyproject.toml`、`meixin_admin/hooks.py`、`modules.txt`、`patches.txt`。
- 核心逻辑：`meixin_admin/scheduling.py`、`permissions.py`、`api.py`、`install.py`、`demo.py`。
- 模型与界面：`meixin_admin/meixin_admin/doctype/**`、`workspace/**`、`desktop_icon/**`、`workspace_sidebar/**`、`translations/zh.csv`。
- 测试：`meixin_admin/tests/**`。
- 部署与隔离环境：`deploy/**`。
- Git 检出规则：`.gitattributes`（容器 shell 脚本固定 LF）。
- 文档：`README_中文.md`、`docs/**`、`docs/M1_ACCEPTANCE.md`、本文件。

## 测试结果

- Python `compileall`：通过。
- 4 个 DocType JavaScript `node --check`：通过。
- 全部 JSON 解析：通过。
- 衍生镜像 `meixin-admin:m1-frappe16.34.0` 构建：通过。
- 最终衍生镜像 `meixin-admin:m1-final-frappe16.34.0` 构建：通过；六个正式应用容器使用镜像 ID `sha256:b5dcea32c81713473225e8d40e5e2cf25c9f6a98b911af245cd94e1650feab97`。
- 隔离站点 App 安装/迁移：通过，日志见 `docs/validation/isolated-install-migrate.log`。
- 集成测试首轮：26 项运行，23 项通过、1 项失败、2 项错误。问题已定位为 Single 类型测试假设和 MariaDB 11.8 并发快照错误；原始失败保留在 `isolated-integration-initial-failures.log`。
- 上述问题及取消夹带修改、缺失时间、权限日志泄露风险已修复。
- 2026-09-21 首次重跑最新 27 项时，Frappe 16 顶层 `frappe.has_permission` 不接受 `print_logs`，27 项均在同一入口报错；修为 `frappe.permissions.has_permission` 的版本兼容调用后再次完整运行，结果为 `Ran 27 tests in 10.790s / OK`。
- 最终复核修复后再次完整运行：`Ran 27 tests in 13.350s / OK`，0 失败、0 错误、0 跳过。
- 浏览器已实际走通工作台→建档→排课→提交→冲突→周课表；全新最终课表页控制台 0 error、0 warn。
- 目标 `frontend` 已完成安装、迁移、服务/API 冒烟及登录后的只读可视验收；全新最终课表页控制台 0 error、0 warn。
- 关机恢复后的两套 Compose `config --quiet`、最终 Nginx 启动脚本 `sh -n`、`git diff --check` 均通过；正式入口再次返回 HTTP 200，九个相关容器运行且 MariaDB 健康。
- 最近正式 WebSocket 日志未匹配 `Invalid origin`、`fetch failed`、Traceback、ERROR、CRITICAL 或 ModuleNotFoundError。

## 未完成事项

- 无 M1 阻塞事项。非管理员业务账号的浏览器点击流程未人工复验；服务器权限矩阵已由隔离集成测试覆盖。
- 正式站点没有业务档案，按只读验收边界未创建临时课程来重复自动结束时间交互；隔离站真实表单和自动化测试均已覆盖。

## 下一步操作

1. 阶段 4B 已完成完整回归并建立独立 checkpoint；按阶段 4 规则暂不 push。
2. 等待用户确认后进入阶段 4C：实现完整 FEFO/FIFO、多课包选择及无可用课包原因分类。
3. 所有 migrate、自动化和写入继续只允许在隔离站执行；正式 `frontend` 保持未触碰，直至候选 checkpoint、完整备份和用户明确批准。

## 明确停止范围

M2 只实现确认排课后的执行、考勤和不可变课消决策流水，已正式部署并冻结。M3 只进入课包、收款、不可变课时权益、M2 同事务扣减、剩余课时和续费；不进入教师工资、总账、会计凭证、支付渠道集成、家长端、发票、CRM、营销、AI 收费或大型经营报表。正式部署前必须重新完整备份并取得用户确认；不得在 `frontend` 运行测试或创建测试业务数据。
