# M1 隔离测试环境

这套环境专门用于自动化验收与虚构数据浏览器检查。它与开发/试点站点 `frontend` 分离，不能把测试命令改为业务站点名。

## 已经现场建立的隔离资源

2026-09-20 在当前 Windows / Docker Desktop 上实际执行：

| 项目 | 实际值 |
| --- | --- |
| Compose 项目 | `meixin_m1_test` |
| 数据库容器 | `meixin_m1_test-db-1` |
| Redis 容器 | `meixin_m1_test-redis-1` |
| Web / 测试容器 | `meixin_m1_test-app-1` |
| Nginx 入口容器 | `meixin_m1_test-frontend-1` |
| 数据库卷 | `meixin_m1_test_test_db` |
| 站点文件卷 | `meixin_m1_test_test_sites` |
| 网络 | `meixin_m1_test_default` |
| 站点 | `test_meixin_m1.localhost` |
| 数据库 | `meixin_m1_test` |
| 框架 | Frappe `16.34.0` |
| 数据库版本 | MariaDB `11.8.9`（镜像 `mariadb:11.8`） |
| Python | `3.14.7` |
| App | `meixin_admin 0.1.0`，宿主机源码通过 Python 路径文件加载 |
| Web 入口 | <http://127.0.0.1:18081> |
| 实时消息 | 经同源入口 `/socket.io` 反向代理到隔离 App 容器 |
| 测试时区 | `Asia/Chongqing`，与已检查的试点站点相同 |

沿用本地已有的 `frappe/erpnext:v16.35.0` 镜像。该镜像包含 ERPNext 源码，但隔离站点只安装 Frappe 与美心 App；本轮业务没有 ERPNext DocType 依赖。日志使用镜像自动创建的独立卷。没有挂载、复制、覆盖或删除 `frappe_docker` 项目的数据库、站点文件卷、网络或配置，也没有导入真实数据。

## Windows PowerShell 命令

在本 App 源码根目录打开 PowerShell：

```powershell
# 第一次启动新隔离环境；已有环境重复运行不会重建数据库。
.\deploy\test-env.ps1 -Action start

# 安装或迁移当前源码；不初始化演示数据。
.\deploy\test-env.ps1 -Action install

# 仅隔离测试站点：中文、Asia/Chongqing、跳过首次安装向导。
.\deploy\test-env.ps1 -Action configure-browser

# 执行有严格站点守卫的真实 Frappe/MariaDB 集成测试。
.\deploy\test-env.ps1 -Action test

# 查看或停止本测试项目。停止保留数据卷。
.\deploy\test-env.ps1 -Action status
.\deploy\test-env.ps1 -Action stop
```

`test` 最终执行的命令是：

```text
bench --site test_meixin_m1.localhost execute meixin_admin.tests.run.run
```

这里**不使用**可能建立全局测试夹具的 `bench run-tests`。执行前同时检查固定测试站点名、`meixin_test_site=1`、`allow_tests=1`、数据库类型，以及独立容器的数据库主机 `db`。运行失败返回非零退出码。测试自己创建虚构批次；普通用例回滚，并发用例只清理由该用例创建并提交的独有测试批次。

`start` 创建的新管理员及数据库密码来自密码学随机数，不自动创建业务账号。密码文件默认位于 App 源码目录两级上方的 `work/meixin-m1-test/`，该目录有忽略全部内容的 `.gitignore`。也可以传入 `-ScratchDirectory` 指向源码以外的私人工作目录。不要把这些密码文件、配置文件、测试卷或任何备份提交到 Git。需要登录时，在本机私下读取 `admin-password`，账号是 `Administrator`；不要把密码贴回聊天或截图。

## 重启与复测

后端通过开发服务器运行，源码修改后可重启**这个测试容器**以重新导入 Python：

```powershell
docker restart meixin_m1_test-app-1
```

容器启动会重新写入当前 bind mount 源码的 Python 路径文件，不访问外网下载构建依赖；不需要修改框架源码或运行前端打包。Desk 表单、DocType JS、Calendar JS 与 Page JS 由 Frappe 原生机制加载。实时消息由同一隔离 App 容器中的 Node 进程提供，Nginx 入口把同源 `/socket.io` 请求转发到该进程；两者随隔离环境一起停止。这里没有为测试运行队列 worker 或 scheduler，M1 排课同步提交不依赖后台任务。

宿主机若配置了 HTTP 代理，可以这样绕过代理核验本地入口：

```powershell
curl.exe --noproxy '*' -I http://127.0.0.1:18081/login
```

登录入口、实时 Socket.IO 握手与浏览器控制台结果应记录在 `M1_ACCEPTANCE.md`。服务连通性验证不能代替浏览器端到端验收。

## 实际测试记录

- `validation/isolated-integration-initial-failures.log` 保留第一次实际运行暴露的问题，未把失败记录改写成通过。
- `validation/isolated-integration-tests.log` 保存当前完整测试输出；最终统计以该文件及 `M1_ACCEPTANCE.md` 为准。
- 安装及迁移已在这个隔离站点实际完成。业务试点站点的安装状态另见 `ENVIRONMENT.md`，不能由隔离环境的成功推断。

本轮未删除任何测试卷；未提供自动清库操作。无需恢复旧数据即可再次启动这个项目。需要真正删除隔离资源时，应另行确认精确项目与卷名。
