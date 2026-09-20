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

### 阶段 3：独立测试环境（已确认可用）

- 创建独立 Compose 项目 `meixin_m1_test`，使用独立 MariaDB/Redis/sites 卷和 `127.0.0.1:18081`。
- 创建严格命名的测试站点 `test_meixin_m1.localhost`，配置 `meixin_test_site=1`、`allow_tests=1`。
- App editable 安装、`install-app`、`migrate` 成功；DocType、Workspace Sidebar、Desktop Icon 同步成功。
- 登录页已通过 HTTP 和浏览器加载；未复制真实站点数据。

## 修改文件

- App 元数据与钩子：`pyproject.toml`、`meixin_admin/hooks.py`、`modules.txt`、`patches.txt`。
- 核心逻辑：`meixin_admin/scheduling.py`、`permissions.py`、`api.py`、`install.py`、`demo.py`。
- 模型与界面：`meixin_admin/meixin_admin/doctype/**`、`workspace/**`、`desktop_icon/**`、`workspace_sidebar/**`、`translations/zh.csv`。
- 测试：`meixin_admin/tests/**`。
- 部署与隔离环境：`deploy/**`。
- 文档：`README_中文.md`、`docs/**`、本文件。

## 测试结果

- Python `compileall`：通过。
- 4 个 DocType JavaScript `node --check`：通过。
- 全部 JSON 解析：通过。
- 衍生镜像 `meixin-admin:m1-frappe16.34.0` 构建：通过。
- 隔离站点 App 安装/迁移：通过，日志见 `docs/validation/isolated-install-migrate.log`。
- 集成测试首轮：26 项运行，23 项通过、1 项失败、2 项错误。问题已定位为 Single 类型测试假设和 MariaDB 11.8 并发快照错误；原始失败保留在 `isolated-integration-initial-failures.log`。
- 上述问题及取消夹带修改、缺失时间、权限日志泄露风险已修改；修复后完整集成测试尚未重跑，不能记为通过。
- 独立测试站点界面仅验证登录页可加载；工作台→建档→排课→提交→冲突→周课表尚未完成。
- 目标 `frontend` 尚未安装 M1；因此现场安装与运行未验证。

## 未完成事项

- 在隔离站点重跑最新集成测试，修复到全部通过；确认并发冲突只有一个提交成功且另一个安全失败/回滚。
- 完成独立测试站点浏览器验收并保存关键截图、控制台结果。
- 创建并更新 `docs/M1_ACCEPTANCE.md`，记录实际命令、结果和未验证项。
- 对最终变更执行五轴代码复核和秘密扫描。
- 在切换试点应用容器前说明中断影响并等待用户最终确认；之后才可安装到 `frontend`。
- 试点安装后再次跑迁移、角色/入口/API 冒烟与人工验收；不在试点站点运行测试套件。

## 下一步操作

1. 从中断点继续：在 `http://127.0.0.1:18081` 登录独立测试站点。
2. 在隔离站点先重跑最新集成测试；若失败，保留日志并修复。
3. 测试通过后执行浏览器流程：美心行政工作台 → 新建档案 → 草稿排课 → 提交 → 制造冲突并确认中文提示 → 周课表筛选。
4. 检查浏览器控制台，截图写入 `docs/validation/`，更新本文件并提交 Git。
5. 汇总可审阅的试点安装结果和中断影响，向用户请求切换应用容器的最终确认。

## 明确停止范围

本轮不进入报名收费、考勤课消、教师工资、微信、校精灵、支付或 AI 自动排课。
