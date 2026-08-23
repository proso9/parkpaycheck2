# AGENTS.md

面向在此仓库协助开发的 AI 与协作者的工程指南。

## 项目概述

停车场系统**异常车辆检测**工具：从系统日志中找出“出场不开闸”且“300 秒内无支付结果下发”的异常车辆，并输出结果。

## 目录结构

```
parkpaycheck_v2/
├── AGENTS.md                       # 本文件
├── check_unopened_gate.py          # 核心检测脚本
├── tests/
│   └── test_check_unopened_gate.py # 无依赖断言式测试
├── document/                       # 日志样例(输入, 不入库)
│   └── system.<日期>.log
└── output/                         # 脚本导出的 CSV(不入库, 自动创建)
    └── 异常车辆_<日期>.csv
```

## 核心脚本说明

`check_unopened_gate.py` 检测流程：

1. **记录A**：解析含“出场处理”且“开闸结果：不开闸”的行，取时间 `T` 与车牌。
2. **记录B**：解析含“==>支付结果下发：”的行，取时间与 JSON 中的 `carNumber`。
3. **判定**：对每条记录A，在记录B中找**车牌相同且时间 ∈ [T, T+300秒]** 的记录；找不到则判为异常。

关键实现点：

- 日志中部分业务行（如“出场处理…”）**不带时间戳**，需继承上方最近一条带时间戳行的时刻。
- 车牌匹配前需**归一化**（去空格、转大写），确保 `carNumber` 与《…》车牌严格对应、不串号。
- 支付下发 `carNumber` 优先 JSON 解析，失败降级为正则兜底。
- 非法时间、缺失车牌均容错跳过。

## 命令行用法

```bash
# 处理 document/ 下所有日志，默认输出到 output/
python check_unopened_gate.py

# 指定单个日志
python check_unopened_gate.py document\system.2026-08-22.log

# 调整判定窗口(秒)与输出目录
python check_unopened_gate.py -w 300 -o output
```

参数均在脚本顶部及 argparse 中，未硬编码。

## 测试

```bash
python -m tests.test_check_unopened_gate
```

测试无第三方依赖，用断言校验各类正常/异常/边界/容错场景，退出码 0 表示全部通过。

## 约定与注意事项

- 输出结果写入 `output/`，**不要**写入 `document/`。
- `document/`、`output/`、`tests/` 均已加入 `.gitignore`。
- 日志为 `system.<YYYY-MM-DD>.log` 命名，输出 CSV 用同日期命名（`异常车辆_<YYYY-MM-DD>.csv`）。
- 修改判定逻辑后请补充/调整测试并确保全部通过。
- 代码注释、文档一律使用简体中文。