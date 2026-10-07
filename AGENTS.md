# AGENTS.md

面向在此仓库协助开发的 AI 与协作者的工程指南。

## 项目概述

停车场系统**异常车辆检测**工具：从系统日志中找出“出场不开闸”且“300 秒内无支付结果下发”的异常车辆，并输出结果。

## 目录结构

```
parkpaycheck_v2/
├── AGENTS.md                       # 本文件
├── README.md                       # 项目介绍、使用说明与依赖安装命令
├── check_unopened_gate.py          # 命令行入口（薄封装，调用 parkcheck.cli）
├── check_gui.py                    # 图形界面入口（tkinter 分页 + APScheduler + 系统托盘）
├── start_gui.vbs                   # GUI 静默启动脚本（pythonw 无黑窗口，可放开机自启）
├── .env.example                    # 本地密钥配置模板（复制为 .env 使用，.env 不入仓库）
├── .github/workflows/              # CI：release.yml（创建 Release 触发测试 + 打包安装程序发布，见「发版打包」一节）
├── installer/
│   └── parkcheck.iss               # Inno Setup 安装包脚本（CI 打单个安装 exe 用，必须保存为 UTF-8 带 BOM）
├── docs/                           # 需求文档等（如 需求文档_数据库上传.md，已纳入版本管理）
├── parkcheck/                      # 检测包
│   ├── __init__.py                 # 公共 API 聚合导出
│   ├── config.py                   # 常量与默认参数（上传配置支持环境变量/.env 覆盖）
│   ├── env.py                      # .env 读取（仅标准库，加载进 os.environ）
│   ├── parser.py                   # 日志解析
│   ├── detector.py                 # 异常判定与入场反查
│   ├── output.py                   # 结果输出（控制台 + CSV）
│   ├── db.py                       # 数据库上传（Cloudflare D1 REST API，仅标准库）
│   ├── state.py                    # 已处理文件状态（跳过未变化的已处理日志）
│   ├── scheduler.py                # 定时任务（APScheduler 封装）
│   └── cli.py                      # 命令行入口与检测编排（run_detection_round，GUI 复用）
├── tests/                          # 测试（已纳入版本管理，发版工作流的测试门禁依赖）
│   ├── test_check_unopened_gate.py # 核心逻辑无依赖断言式测试
│   ├── test_scheduler.py           # 定时任务模块测试（依赖 apscheduler）
│   ├── test_state.py               # 已处理状态模块测试（无依赖）
│   ├── test_db.py                  # 数据库上传模块测试（无依赖，HTTP 层 mock）
│   └── test_env.py                 # .env 读取模块测试（无依赖）
├── document/                       # 日志样例(输入, 不入库)
│   └── system.<日期>.log
└── output/                         # 脚本导出的 CSV(不入库, 自动创建)
    └── 异常车辆_<日期>.csv
```

## 检测流程

整个流程在 `parkcheck` 包内完成，入口脚本仅做薄封装：

1. **record_a**（`parser.parse_log`）：解析含“出场处理”且“开闸结果：不开闸”的行，取时间 `T` 与车牌。
2. **record_b**（`parser.parse_log`）：解析含“==>支付结果下发：”的行，取时间与 JSON 中的 `carNumber`。
3. **判定**（`detector.find_anomalies`）：对每条记录A，在记录B中找**车牌相同且时间 ∈ [T, T+300秒]** 的记录；找不到且本次出场能关联到应交金额（费用行）才判为异常，无应交金额的出场不输出。
4. **入场反查**（`detector.find_anomalies`）：以相机`方向：入口`扫描（含重复上传）作为入场事件来源，`入场车牌号`行兜底；同一辆车 5 秒内的重复入口扫描归并为同一次入场并取第一次，本次出场取之前最近一次入场事件的时间；无匹配入场用 `-` 标记（窗口 `ENTRY_DEDUP_WINDOW` 可调）。
5. **可疑标记**（`detector.compute_anomaly`）：解析出场相机扫描后的"停车时间:X天,剩余:Y分钟"，换算成总分钟数；与"入场→出场"实际时长比较，仅当系统停车时间**明显**大于实际时长（至少多出 `MIN_PARK_TIME_DEVIATION` 分钟，如被门卫遥控放行）时在输出中标记"异常"为 1，否则为 0。

各模块职责：`config.py` 存放关键词与参数默认值，`parser.py` 负责时间/车牌/费用/入场提取与拆行，`detector.py` 负责异常判定与入场反查，`output.py` 负责控制台与 CSV 输出，`db.py` 负责 Cloudflare D1 上传（REST API 客户端、建表、去重键、批量插入），`state.py` 负责已处理日志文件的状态记录与排除，`cli.py` 负责命令行入口与**一轮检测的统一编排**（`run_detection_round`：逐文件 解析→判定→输出→按开关决定标记时机，CLI 与 GUI 共用同一函数，两条入口行为保证一致）。

关键实现点：

- 日志中部分业务行（如“出场处理…”）**不带时间戳**，需继承上方最近一条带时间戳行的时刻。
- 车牌匹配前需**归一化**（去空格、转大写），确保 `carNumber`、入场车牌与《…》车牌严格对应、不串号。
- 支付下发 `carNumber` 优先 JSON 解析，失败降级为正则兜底。
- 入场以相机`方向：入口`为准（含“重复上传”等重复上报），避免“出场不开闸重试”被误判为新的停车周期。
- 出场记录会关联“用户需支付费用”（`用户需支付费用:金额`）。费用行紧跟在本次出场的“方向：出口”相机扫描之后出现（支付流程还会在出场处理之后重算一次费用，同关键词再现），故解析到**新的出口扫描行即重置**待关联状态，出场处理仅关联**本次出口扫描之后**的最近一条费用行，不串到其他车辆；本次出场无费用行则记 `-`，且因无应交金额不判为异常。
- 出场记录会关联系统“停车时间”（`停车时间:X天,剩余:Y分钟`）。关联规则同费用行：仅取本次出口扫描之后的最近一条，用于与“入场→出场”实际时长比较判断可疑标记；无则不比较（异常标记为 0）。
- 非法时间、缺失车牌均容错跳过。

输出列：`入场时间, 出场时间, 车牌号, 用户需支付费用, 异常`（入场时间找不到显示 `-`，无关联费用显示 `-`；“异常”为 1 表示系统停车时间明显大于实际时长，可疑）。

## 数据库上传（Cloudflare D1）

可选功能（默认关闭，需求见 `docs/需求文档_数据库上传.md`）：把异常记录上传到 Cloudflare D1 数据库，供另一独立项目读取展示。实现要点：

- **仅标准库**：`parkcheck/db.py` 用 `urllib.request` 直接调 D1 REST API（`POST /accounts/{id}/d1/database/{id}/query`），不部署 Worker、不引入第三方依赖；HTTP 层通过 `opener` 参数注入，便于测试 mock。
- **只插入新记录，永不覆盖**：`INSERT OR IGNORE` + 唯一去重键 `dedup_key = sha256("{log_file}|{exit_time}|{car_number}")`；本工具对已有记录不做任何 UPDATE，`status`/`remark`（0/NULL 默认值之外）由展示项目维护。
- **表自动创建**：每次上传会话先发一次 `CREATE TABLE IF NOT EXISTS anomalies`（幂等）；日志内 `HH:MM:SS` 时间入库前用文件名日期补全为 `YYYY-MM-DD HH:MM:SS` 全格式；`entry_time`/`fee` 为 `-` 时存 `NULL`。
- **上传成功才标记已处理（统一上传）**：上传开启时流程为 扫描全部日志 → 输出 CSV → **汇总所有异常记录统一分批上传 D1** → 全部成功才统一标记已处理；任一批失败则本轮所有日志均不标记，打印明确错误后结束本轮，下一轮重新检测并重试（去重键兜底不产生重复）。关闭上传时行为与无上传版本完全一致（逐文件立即标记）。注意 D1 单次查询最多 100 个绑定参数，每行 8 个参数，单批最多 12 行（批量大小配置超过时自动收紧）。
- **配置**：`config.py` 默认值（`UPLOAD_ENABLED / CF_ACCOUNT_ID / CF_DATABASE_ID / CF_API_TOKEN / DB_BATCH_SIZE`），CLI 参数 `--upload --cf-account --cf-database --cf-token --db-batch` 可覆盖，GUI「数据库上传」页签可编辑。账户 ID / 数据库 ID / Token 任一为空视为未配置上传（等同关闭，提示一次）。
- **本地密钥持久化（.env）**：`parkcheck/env.py`（仅标准库）在 `config.py` 导入时加载项目根目录 `.env`（可选，缺失静默忽略，已存在的环境变量不被覆盖）。上传 5 项配置优先级：**显式命令行参数 > 环境变量（含 .env）> config.py 默认值**；模板见 `.env.example`，`.env` 已加入 `.gitignore`，不入仓库、不随「导出配置」JSON 导出。
- **API Token 安全**：Token 绝不随 GUI「导出配置」JSON 导出（见 `check_gui.upload_export_fields`），导入配置时忽略该字段。

## 命令行用法

```bash
# 处理 document/ 下所有 system.<日期>.log（platform.* 及无日期日志不分析），默认输出到 output/
python check_unopened_gate.py

# 指定单个日志
python check_unopened_gate.py document\system.2026-08-22.log

# 调整判定窗口(秒)与输出目录
python check_unopened_gate.py -w 300 -o output

# 细调可疑标记阈值(分钟)与入场去重窗口(秒)
python check_unopened_gate.py --min-deviation 5 --entry-dedup 5

# 忽略已处理记录，强制重新处理所有日志
python check_unopened_gate.py --reprocess

# 开启数据库上传（Cloudflare D1），上传成功才标记日志已处理
python check_unopened_gate.py --upload --cf-account <账户ID> --cf-database <库ID> --cf-token <API_Token>

# 也可通过模块方式运行
python -m parkcheck.cli -w 300 -o output
```

参数均在 `parkcheck/config.py` 定义默认值，并在 `parkcheck/cli.py` 的 argparse 中提供覆盖，未硬编码。

**已处理日志排除**（`parkcheck/state.py`，CLI 与 GUI 默认启用）：已处理日志的状态（大小 + 修改时间）记录在输出目录下的 `.processed.json`，内容未变化的日志自动跳过；仍在追加写入的日志（如当天日志）不受影响，会正常重新检测。`--reprocess` 可忽略记录强制重跑；换输出目录即重新记录，互不干扰。状态键按平台规则归一大小写（Windows 下同一文件不同大小写写法视为同一记录）。

**单文件失败隔离**（CLI 与 GUI 共用 `run_detection_round`，行为一致）：任一日志解析/输出失败只跳过该文件并打印错误，**不中断本轮**，其余日志照常处理；失败文件不标记已处理（上传模式下也不进入汇总），下一轮自动重试。

## 图形界面与定时任务

```bash
# 启动图形界面（含路径/参数/定时任务配置，配置可导入导出 JSON）
python check_gui.py
```

图形界面（`check_gui.py`）在命令行能力基础上增加**分页配置**与**系统托盘后台运行**：

- 分页配置（`ttk.Notebook`）：路径配置 / 判定参数 / 定时任务 / 数据库上传 / 运行与输出 五个页签，配置相互隔离、互不干扰。
- **界面主题与全局操作栏**：基于 clam 主题定制的浅色界面（微软雅黑字体、白底卡片分组、蓝色主色、参数悬浮提示、Windows DPI 感知，窗口尺寸随系统缩放），零新增依赖、不改变打包流程；窗口底部「开始检测 / 清空输出 / 打开输出目录」操作栏与状态显示（最近检测、定时任务状态）在任意页签可用，手动检测时自动切换到运行与输出页；输出区为只读回显（纵向 + 横向滚动）。
- **排除已处理日志**（路径配置页勾选，默认勾选）：勾选后内容未变化的已处理日志自动跳过，定时任务不会重复处理旧日志；随「导出/导入配置」JSON 一同保存恢复（字段 `skip_processed`）。
- **数据库上传**（数据库上传页，见上文「数据库上传（Cloudflare D1）」一节）：勾选「启用数据库上传」并填写账户 ID / 数据库 ID / API Token（掩码显示）后，手动检测与定时检测均会先上传再标记已处理；上传字段随「导出/导入配置」JSON 保存恢复（字段 `upload_enabled / cf_account / cf_database / db_batch`），**API Token 绝不导出，导入时忽略**。
- **定时任务**（基于 APScheduler，见 `parkcheck/scheduler.py`）：
  - 勾选「启用定时任务」即按配置在后台自动定时检测，取消勾选即停止。
  - 支持两种方式：
    - **间隔运行**：每隔 N 秒/分/时运行一次；
    - **每天固定时间**：每天 HH:MM（如 08:00）运行一次。
  - 定时检测每次触发会**实时读取**当前界面的日志目录、输出目录与判定参数，修改后无需重启即生效。
  - 状态栏显示「下次触发」时间。
- **系统托盘后台运行**（pystray）：
  - 点击窗口关闭按钮 → 自动收纳到右下角系统托盘（`root.withdraw()`），后台定时任务继续运行；
  - 托盘菜单：「显示主窗口」恢复窗口、「退出程序」完全退出；
  - 托盘不可用（未安装 pystray/Pillow）时回退为最小化到任务栏；
  - 菜单栏「配置 → 退出程序」为显式退出路径；完全退出时会停止调度器并移除托盘图标。
- 定时配置随「导出/导入配置」JSON 一同保存恢复（字段：`schedule_enabled / schedule_type / schedule_value / schedule_unit / schedule_time`）。
- 调度逻辑封装在 `SchedulerManager`（`start_interval` / `start_daily` / `stop` / `running` / `next_run`），与手动检测共用同一检测流程，可独立复用。

## 测试

```bash
# 核心逻辑测试（无第三方依赖）
python -m tests.test_check_unopened_gate

# 定时任务模块测试（依赖 apscheduler）
python -m tests.test_scheduler

# 已处理状态模块测试（无第三方依赖）
python -m tests.test_state

# 数据库上传模块测试（无第三方依赖，HTTP 层 mock，不依赖真实网络）
python -m tests.test_db

# .env 读取模块测试（无第三方依赖）
python -m tests.test_env
```

各套测试均用断言校验，退出码 0 表示全部通过。

测试脚本会在启动时把 `sys.stdout` / `sys.stderr` 重配置为 UTF-8；Windows 运行器（`windows-latest`）默认控制台编码为 cp1252，直接打印中文会抛 `UnicodeEncodeError`，新增测试文件时应保留这段兜底（或在 CI 中设置 `PYTHONIOENCODING=utf-8`，workflow 已配置）。

## 发版打包（GitHub Actions）

在 GitHub 上创建并发布 Release（tag 命名 `v主.次.修订`，可让 GitHub 在建 Release 时顺带创建 tag）即自动触发 `.github/workflows/release.yml`：安装 Python 3.12 与依赖 → 运行全部测试（门禁，任一失败即终止，不出包）→ Nuitka `--standalone`（**明确不用 Onefile**）打包 `check_gui.py`（仅 Windows 平台）→ Inno Setup 把 standalone 产物打成**单个安装程序** `parkcheck-setup-<tag>-windows-x64.exe` → 附加到该 Release（发布说明在建 Release 时编写，workflow 不再自动追加）。也可在 Actions 页面手动触发补构建（填版本号，可选指定分支/tag）。安装器为用户级安装：默认装到 `%LOCALAPPDATA%\parkcheck`，无需管理员权限（GUI 默认日志/输出目录在安装目录下，必须保证当前用户可写），可选创建桌面快捷方式，自带卸载器。

**发版规范（AI 协作者每次发版前逐条核对）**：

- 发版前先在本地跑通全部测试；workflow 中测试是打包门禁，失败即不出包——注意 Release 已随创建而公开存在，测试/打包失败会留下一个暂无产物的 Release，此时优先去 Actions 页面重跑该次 workflow 补传产物。
- tag/Release 命名严格为 `v主.次.修订` 三段数字（如 `v1.2.3`），不带 `-` 后缀：workflow 会把 tag 去掉 `v` 后注入 exe 的文件/产品版本属性，遇到 `-` 会被截断。
- 确需重新发版必须先删除远端 Release 与对应 tag，再重新创建；不要随意删除重打。
- 打包目标只有 `check_gui.py`（GUI）一个入口、仅 Windows 平台；如需新增入口或平台，先修改 workflow 与 `installer/parkcheck.iss` 再发版。
- 修改运行依赖（`apscheduler / pystray / Pillow`）、Nuitka 参数、安装脚本或打包流程时，必须同步修改 `.github/workflows/release.yml`（及 `installer/parkcheck.iss`）并保持本节描述一致。
- workflow 内打包参数有讲究，勿凭记忆删改：`--enable-plugin=tk-inter`（tkinter 独立打包必需）；`--include-package=pystray` 与 `--include-package=PIL`（两者均有运行期动态导入，静态分析会漏收子模块，缺失会导致托盘/图标功能崩溃）；`--windows-console-mode=disable`（GUI 不弹控制台）。
- 触发只监听 `release: published`，**不监听 `push: tags`**：创建 Release 同时新建 tag 会同时触发两种事件，双重监听会导致一次发版跑两遍构建。
- `installer/parkcheck.iss` 必须保存为 UTF-8（带 BOM），否则中文 AppName 会按 ANSI 解析而乱码；Inno Setup 由 CI 用 `choco install innosetup` 安装。
- Nuitka 编译较慢（首次约 10～20 分钟）属正常现象，不是失败；排查构建问题以 Actions 运行日志为准。
- 从 Release 下载的安装程序未做代码签名，浏览器下载与 SmartScreen 会提示"未知发布者"，属预期现象。

## 约定与注意事项

- 输出结果写入 `output/`，**不要**写入 `document/`。
- `document/`、`output/` 已加入 `.gitignore`；`tests/`、`docs/` **纳入版本管理**（发版工作流的测试门禁在 CI 检出仓库后运行，缺文件会直接失败）。
- **日志选取**：扫描目录时只分析符合 `system.<YYYY-MM-DD>.log` 命名的文件；`platform.*` 等其他前缀、无日期日志（如 `system.log`、`platform.log`）一律排除，不进入分析逻辑（规则在 `parkcheck/config.py` 的 `is_analyzed_log_name`，CLI 与 GUI 共用，大小写不敏感——`.LOG` 大写扩展名同样收集）；显式指定的单个日志文件不做命名过滤。输出 CSV 用同日期命名（`异常车辆_<YYYY-MM-DD>.csv`）。
- 修改判定逻辑后请补充/调整测试并确保全部通过。
- **发版发布**：在 GitHub 上创建并发布 Release（tag 命名 `v主.次.修订`）即触发自动打包，发版前逐条核对「发版打包（GitHub Actions）」一节的规范。
- 图形界面依赖第三方库：定时任务 `apscheduler`、系统托盘 `pystray`/`Pillow`（安装命令见 `README.md`），核心检测逻辑与数据库上传（`db.py`）仍保持无第三方依赖。
- API Token 等涉密信息不得写入代码、配置文件或「导出配置」JSON。
- 代码注释、文档一律使用简体中文。