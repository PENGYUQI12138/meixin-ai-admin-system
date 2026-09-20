# 美心行政 M1 开发进度

最后更新：2026-09-21（Asia/Shanghai）

## 恢复工作规则

每次恢复先执行：

```powershell
Get-Content PROGRESS.md
git status --short --branch
git log --oneline -5
```

只从“下一步操作”继续。每完成一个独立阶段，立即更新本文件；重要节点执行 Git commit。不得把未运行的检查记为通过，不得在 `frontend` 运行测试套件。

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
- 隔离站点 App 安装/迁移：通过，日志见 `docs/validation/isolated-install-migrate.log`。
- 集成测试首轮：26 项运行，23 项通过、1 项失败、2 项错误。问题已定位为 Single 类型测试假设和 MariaDB 11.8 并发快照错误；原始失败保留在 `isolated-integration-initial-failures.log`。
- 上述问题及取消夹带修改、缺失时间、权限日志泄露风险已修复。
- 2026-09-21 首次重跑最新 27 项时，Frappe 16 顶层 `frappe.has_permission` 不接受 `print_logs`，27 项均在同一入口报错；修为 `frappe.permissions.has_permission` 的版本兼容调用后再次完整运行，结果为 `Ran 27 tests in 10.790s / OK`。
- 最终复核修复后再次完整运行：`Ran 27 tests in 13.350s / OK`，0 失败、0 错误、0 跳过。
- 浏览器已实际走通工作台→建档→排课→提交→冲突→周课表；全新最终课表页控制台 0 error、0 warn。
- 目标 `frontend` 已完成安装、迁移及服务/API 冒烟；登录后的可视点击检查尚待完成。

## 未完成事项

- 用户在已打开的 `http://localhost:8080` 登录页完成登录后，检查目标工作台、表单入口和周课表；不创建真实数据。

## 下一步操作

1. 用户在已打开的目标站点登录页完成登录。
2. 完成工作台、档案新建入口、排课新建入口、周课表和控制台的可视冒烟。
3. 更新验收记录与本文件，提交最终 Git commit，然后按 M1 范围停止。

## 明确停止范围

本轮不进入报名收费、考勤课消、教师工资、微信、校精灵、支付或 AI 自动排课。
