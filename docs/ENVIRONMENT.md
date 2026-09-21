# 环境核查与操作记录

核查日期：2026-09-20。本文件只记录现场实际命令观察，不包含密码、密钥、完整配置或业务表数据。

## 已确认

| 项目 | 现场结果 |
|---|---|
| 宿主机 | Windows 10.0.26200，PowerShell |
| Docker / Compose | Linux Docker Engine 29.8.0；Compose v5.5.1 |
| 原部署仓库 | `D:\FrappeProject\frappe_docker` |
| 原 Compose 项目/文件 | `frappe_docker`；`D:\FrappeProject\frappe_docker\pwd.yml` |
| 仓库状态 | 检查时 `git status --porcelain=v1` 为空；未修改原仓库 |
| 规范 | 已读 README.md、CONTRIBUTING.md、custom-app 说明；在项目及任务父目录未发现 AGENTS.md |
| 原镜像 | `frappe/erpnext:v16.35.0` |
| Frappe / ERPNext | 实际 `bench version` / `list-apps`：16.34.0 / 16.35.0 |
| Bench / Python | 5.31.0 / 3.14.7（容器内） |
| 唯一站点 | `frontend`，用户已明确确认开发/试点站点并授权启动、备份、安装 |
| 数据库 | MariaDB 11.8.9-MariaDB-ubu2404 |
| 已安装 App（修改前） | frappe、erpnext |
| 自定义类型 | custom DocType 0、MX DocType 0；已有 Custom Field 12，未覆盖 |
| 实际站点时区 | Asia/Chongqing，当前中国 UTC+8；未更改 |
| 站点入口 | http://localhost:8080 |
| 容器源码根目录 | `/home/frappe/frappe-bench/apps`，原含 frappe、erpnext |

修改前：数据库、后端、队列和 Redis 停止；frontend/websocket 重启中，scheduler 运行。启动原有容器后均恢复运行，数据库健康。没有重装站点、删除旧系统或删除卷。

启动执行 `docker compose ... start` 时 Compose 同时重新运行了原 `configurator` 依赖，重新应用原 Compose 中的服务连接配置；这不是计划中的单纯进程启动。后续操作改用 `docker start` 或明确 `--no-deps`，避免再触发初始化依赖。没有主动编辑原配置内容。

宿主机工具额外变化：代码生成过程中首次调用 `python` 触发 Windows Python Install Manager，自行安装/更新为 Python 3.14.7，管理器为 26.3；没有因此更新任何 Frappe/ERPNext 容器。后续验证使用明确的容器解释器。

## 持久化

原项目卷：

- `frappe_docker_sites` → `/home/frappe/frappe-bench/sites`（站点、文件、站点配置）。
- `frappe_docker_logs` → `/home/frappe/frappe-bench/logs`。
- `frappe_docker_db-data` → `/var/lib/mysql`。

App 原目录不在卷中，所以不能只拷贝进临时容器。交付源码在本机本任务 `outputs/meixin_admin`；衍生镜像携带同一源码和 Python 包，所有六个应用服务使用该镜像，数据库/Redis/原卷保持沿用。镜像构建不会修改原站点；切换应用容器须用户确认。

测试项目另建 `meixin_m1_test`，完全独立网络和卷，详情见 TEST_ENVIRONMENT.md；未复制真实站点数据库或文件。

## 已执行备份

在任何 App 安装/迁移前，2026-09-20 19:07:48 运行：

```powershell
docker exec frappe_docker-backend-1 bash -lc 'cd /home/frappe/frappe-bench && bench --site frontend backup --with-files'
```

返回成功，包括数据库、公有文件、私有文件、站点配置：

```text
20260920_190748-frontend-database.sql.gz
20260920_190748-frontend-files.tar
20260920_190748-frontend-private-files.tar
20260920_190748-frontend-site_config_backup.json
```

容器位置 `/home/frappe/frappe-bench/sites/frontend/private/backups/`，属于持久卷。另复制到宿主机 **`D:\FrappeProject\backups\meixin-m1-20260920`**，在原仓库与交付源码之外，未入 Git。已计算 SHA256；请把该目录作为敏感备份保存，不公开上传，不把配置内容贴到聊天。

## Docker 安装命令（已获确认并执行）

在 PowerShell，先进入交付 App 根目录，再运行：

```powershell
docker build -f deploy/Dockerfile -t meixin-admin:m1-frappe16.34.0 .
docker compose --project-name frappe_docker -f D:/FrappeProject/frappe_docker/pwd.yml -f ./deploy/pilot-compose.yml up -d --no-deps backend frontend websocket queue-short queue-long scheduler
docker exec frappe_docker-backend-1 bash -lc 'cd /home/frappe/frappe-bench && grep -qxF meixin_admin sites/apps.txt || printf "%s\n" meixin_admin >> /home/frappe/frappe-bench/sites/apps.txt'
docker exec frappe_docker-backend-1 bash -lc 'cd /home/frappe/frappe-bench && bench --site frontend install-app meixin_admin && bench --site frontend migrate && bench --site frontend clear-cache'
```

上面的 `--no-deps` 很重要：不启动 configurator/create-site，也不重建数据库。首次安装后 `sites/apps.txt` 追加一行 `meixin_admin`，保留原 App 列表。以后源码改动重新构建衍生镜像、切换应用容器，执行 `migrate`；不要再用只有原 `pwd.yml` 的 `up` 覆盖自定义镜像。

### 2026-09-21 实际安装结果

- 用户确认影响后，固定基础镜像 digest 的 `meixin-admin:m1-frappe16.34.0` 构建成功。
- 仅使用 `--no-deps` 重建 `backend`、`frontend`、`websocket`、`queue-short`、`queue-long`、`scheduler`；未运行 configurator/create-site，数据库与两个 Redis 容器未重建。
- `install-app meixin_admin`、`migrate`、`clear-cache` 均以退出码 0 完成；安装后重启六个应用服务，使其加载新 App。
- 站点 `list-apps` 为 frappe 16.34.0、erpnext 16.35.0、meixin_admin 0.1.0，原框架版本未变化。
- 九个相关容器均运行，MariaDB 健康；首页和登录页 HTTP 200，Socket.IO 返回有效握手包。
- 7 个 MX DocType、两个业务角色、Workspace、Workspace Sidebar、Desktop Icon 和机构默认值均已只读确认；实际站点时区仍为 `Asia/Chongqing`。
- Guest 调用美心上下文及日历 API 均返回 HTTP 403；最近应用容器日志未发现 Traceback、ERROR、CRITICAL 或 ModuleNotFoundError。
- 安装没有执行演示初始化，也没有创建业务账号；未在 `frontend` 运行测试套件。

### 2026-09-21 关机恢复与 Socket.IO 最终状态

- Windows 正常关机后重新核对，而非重复安装。六个应用容器均使用最终镜像 `meixin-admin:m1-frappe16.34.0`，镜像 ID `sha256:a56341d2de5321e8cc6d9944a9e9e4d53fb3cad77bb9e43e4c317fd8bd143c9c`。
- MariaDB 健康，Redis 运行；`frappe_docker_db-data`、`frappe_docker_sites`、`frappe_docker_logs` 及 Redis 持久化卷仍存在。六个应用容器继续挂载原 `sites` 与 `logs` 卷。
- 正式 Nginx 的 `/socket.io` 仍代理到 `websocket:9000`，并将认证所需的 Origin/Host 设置为容器网络内可达且相互匹配的 `http://frontend:8080` / `frontend:8080`。这避免独立 WebSocket 容器把公开地址 `localhost:8080` 解释为自身。
- 正式入口 `http://localhost:8080` 返回 HTTP 200；全新登录态浏览器页面完成工作台、空白新建表单、机构设置和周课表只读验收，控制台 0 error、0 warn。
- 恢复检查没有再次执行 `install-app`、`migrate`、测试套件、演示初始化或任何数据写入；没有删除、恢复或重建持久化卷。

原服务日常启动（只启动存在的容器，不触发 Compose 初始化依赖）：

```powershell
docker start frappe_docker-db-1 frappe_docker-redis-cache-1 frappe_docker-redis-queue-1
docker start frappe_docker-backend-1 frappe_docker-websocket-1 frappe_docker-queue-short-1 frappe_docker-queue-long-1 frappe_docker-scheduler-1 frappe_docker-frontend-1
```

数据库先健康后再启应用。浏览器入口为原端口 8080，无新公开部署。

## 失败与恢复方法（本轮没有执行恢复）

安装/迁移失败先保留错误和现有卷，停止业务写入，修复前进。Frappe DDL/迁移不是单一可回滚事务，因此不要宣称一个 rollback 能撤销整个安装。

恢复数据库和文件会覆盖目标状态，必须再次确认 `frontend` 和所选备份批次。核对备份存在、可读、摘要一致后，使用当前同版本镜像执行：

```bash
bench --site frontend restore sites/frontend/private/backups/20260920_190748-frontend-database.sql.gz \
  --with-public-files sites/frontend/private/backups/20260920_190748-frontend-files.tar \
  --with-private-files sites/frontend/private/backups/20260920_190748-frontend-private-files.tar
```

数据库管理凭据仅在本机受保护提示中输入，不放在聊天、脚本、Git或命令示例里。站点配置备份包含敏感内容：如确需恢复，先保留当前配置，再人工核对同一站点的数据库连接和加密密钥后恢复，禁止无脑覆盖。恢复后确认应用版本匹配并 `bench --site frontend migrate`、`clear-cache`。

如果恢复到安装 M1 之前，数据库、原文件、配置、App 列表及应用镜像要成套对齐；不要仅退镜像留下数据库仍引用 meixin_admin。切勿用 uninstall-app、reinstall 或删除卷替代恢复。

实际安装状态、测试结果与未验证项以 M1_ACCEPTANCE.md 最后记录为准。

## M1 最终人工验收整改部署状态

2026-09-21 已在独立 `test_meixin_m1.localhost` 完成新增学生字段、年级/学科标准选项、排课默认结束时间和业务用户时区对齐的迁移与验证。用户随后明确确认正式部署，现已完成：

- 新备份目录：`D:\FrappeProject\backups\meixin-m1-final-20260921_095041`，包含数据库、公有文件、私有文件、站点配置及 `SHA256SUMS.txt`；数据库 gzip、两个 tar 和配置 JSON 均已实际读取验证。
- 最终镜像：`meixin-admin:m1-final-frappe16.34.0`，ID `sha256:b5dcea32c81713473225e8d40e5e2cf25c9f6a98b911af245cd94e1650feab97`。
- 仅重建六个应用容器；数据库容器 ID 保持 `1941326f3201…`，redis-cache 保持 `4630dbea8b51…`，redis-queue 保持 `a0762a1e8554…`，原持久化卷未改动。
- `bench --site frontend migrate` 与 `clear-cache` 各执行一次并成功；未运行 `install-app`、configurator 或 create-site。
- 正式站点元数据、站点/Administrator 时区、HTTP、工作台、空白新建表单和周课表已只读验证；没有创建业务记录，也没有在正式站点运行测试套件。

若仅需回退 migrate 前的应用镜像，可把 `deploy/pilot-compose.yml` 的六个镜像标签临时改回 `meixin-admin:m1-frappe16.34.0` 后使用同一 `--no-deps` 命令重建六个应用容器。由于本轮 migrate 已改变 DocType schema，若需要恢复数据库/文件，必须停写并经明确确认后使用本节记录的同一批最终备份；不得只退镜像并声称数据库也已回退。
