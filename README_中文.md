# 美心 Frappe 行政系统 M1

独立 App `meixin_admin`，模块 `Meixin Admin`。使用现有 Frappe Desk，不修改 Frappe/ERPNext 核心。范围仅包括中文工作台、学生/教师/课程/教室档案、单节排课、周课表、权限、演示与测试。

适配现场：Frappe **16.34.0**、ERPNext **16.35.0**、Python **3.14.7**、MariaDB **11.8.9**，镜像 `frappe/erpnext:v16.35.0`。不执行框架升级。

## 先读这里

- 环境、备份与本轮部署记录：[docs/ENVIRONMENT.md](docs/ENVIRONMENT.md)。
- 并发、权限与后续数据边界：[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。
- 实际测试与点击验收：[docs/M1_ACCEPTANCE.md](docs/M1_ACCEPTANCE.md)。
- 下一步及停止范围：[docs/NEXT_STEPS.md](docs/NEXT_STEPS.md)。
- 隔离测试环境：[docs/TEST_ENVIRONMENT.md](docs/TEST_ENVIRONMENT.md)。

## 日常使用

1. 使用已有账号登录试点站点 `http://localhost:8080`。由系统管理员给行政分配 `Meixin Manager` 或 `Meixin Scheduler` 角色，不要给所有人 System Manager。
2. 搜索并打开「美心行政」工作台，从入口进入真实的档案列表，点击「新建」。先建课程、教师、教室和学生。
3. 在「排课」中新建，选择课程、教师、教室，填写起止时间，并添加至少一名学生。「保存」是草稿，不占用资源；「提交」才确认排课。
4. 冲突时会显示中文提示。相邻时段允许，跨午夜正确按完整日期判断。结束时间必须晚于开始时间。
5. 工作台进入「周课表」；可切换日期并筛选教师、教室。草稿和确认颜色不同，取消记录不显示在有效课表。
6. 已确认排课不能直接更改。美心管理员可「取消」，之后「修订」成新草稿，再提交；取消的原记录保留。
7. 「美心设置」仅管理员维护机构名称，显示实际站点时区。本轮保持原 `Asia/Chongqing`。原生表单和日历按当前用户时区显示，服务端转换后按站点时区存储；表单会同时提示两者，中国排课的行政账号应使用中国时区。

## 安装和迁移原则

先备份站点数据库、公私文件和站点配置；不要使用 `reinstall`、`drop-site`、`down -v`。现有相同名字的 DocType/模块会阻止首次安装，需先检查兼容性。安装与迁移不创建演示数据或账号。

现有 Docker `pwd.yml` 的 App 目录在镜像层，仅拷贝代码进容器会在重建时丢失。交付包含基于现有版本的衍生镜像 `deploy/Dockerfile` 与独立覆盖文件 `deploy/pilot-compose.yml`。使用它会重建应用容器、短时中断网页和队列；必须先确认后执行，数据库/站点/日志卷沿用原项目。

### 常规 Bench 环境（已有同版本环境时）

将本目录复制到 `<bench>/apps/meixin_admin`，从 Bench 根目录执行：

```bash
./env/bin/pip install --no-deps -e apps/meixin_admin
# 仅在 sites/apps.txt 尚无 meixin_admin 时添加一行；不要覆盖现有内容。
grep -qxF meixin_admin sites/apps.txt || printf '%s\n' meixin_admin >> sites/apps.txt
bench --site <已确认站点> backup --with-files
bench --site <已确认站点> install-app meixin_admin
bench --site <已确认站点> migrate
bench --site <已确认站点> clear-cache
```

后续更新使用 `migrate`，不要再次首次安装或强制覆盖。开发 Bench 由 `bench start` 启动；本机 Docker 由容器原启动命令管理，不在现有生产进程旁另开 `bench start`。

本 App 没有独立前端依赖或打包脚本，DocType JS、Workspace 和翻译由 Frappe 加载；无额外 npm 包。

## 手动演示（不会自动运行）

在已安装 App 的开发/试点站点执行：

```bash
bench --site frontend execute meixin_admin.demo.initialize
bench --site frontend execute meixin_admin.demo.preview_cleanup
```

初始化建立 6 名虚构学生、2 名教师、2 门课程、2 间教室、3 节确认排课，使用同一 `demo_batch`。不填真实电话、不创建账号。重复执行不重复造数据；已有不完整批次会拒绝覆盖。清理函数**仅预览**，不会删除。具体默认日期、批次与实测结果见验收文档。

## 测试

只在独立站点 `test_meixin_m1.localhost` 运行，要求 `meixin_test_site=1` 和 `allow_tests=1`。测试入口在发现或写入任何测试数据前验证三项条件，拒绝 `frontend`。

```bash
bench --site test_meixin_m1.localhost execute meixin_admin.tests.run.run
```

此入口不使用可能建立全局样例数据的框架测试发现流程。真实数据库与多进程事务测试使用隔离数据库；测试成功不代表浏览器或现场自动验收通过，二者分别记录。

## 恢复

备份文件在 Git 外。恢复会覆盖目标数据库，必须再次确认具体站点和备份，不能把失败时自动恢复当作安全操作。详见环境文档。任何数据库恢复都要匹配同一批次的文件和配置；已有数据的升级失败优先修复前进，禁止卸载 App 来“清理”错误。

## M1 停止范围

未实现收费、报名、考勤课消、教师工资、微信、校精灵对接、支付或 AI 排课；不创建这些业务的空界面。M1 完成后停止。
