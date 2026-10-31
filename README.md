# 羊羊助手（SheepSolver fork）

基于 NB-Dragon/SheepSolver（AGPL-3.0）。抓包取得地图与种子 → 识别棋盘 → 求解完整路线 → 后台点击执行。
当前支持：大世界静态关（含“一次移出”道具规划，执行尚未接入）、每日关 / 滚动规划；变色、电量规则作为求解规则的一部分保留。

最新进度与未完成事项见 `docs/交接文档-2026-10-31.md`。

## 来源与致谢

本项目 fork 自 **[NB-Dragon/SheepSolver](https://github.com/NB-Dragon/SheepSolver)**，衷心感谢原作者 NB-Dragon 的开源工作。

原项目从 2022 年起持续维护，提供了本项目赖以起步的全部基础：地图与种子协议解析、按真实种子还原牌型的洗牌逻辑、静态皮肤/砖块数据，以及 `normal` 回溯解题算法。这些代码保留在 `business/`、`core/`、`helper/` 中，至今仍被原版快照求解和规则回放直接使用；没有原项目就没有这里的一切。

- 原项目 README（含原作者的说明与交流渠道）完整保留在 [`docs/原项目README.md`](docs/原项目README.md)。
- 本仓库保留了原项目的全部提交历史，原作者的每一次提交都可以在 git 记录中查到。
- 本项目沿用原项目的 **AGPL-3.0** 许可证（见 [`LICENSE`](LICENSE)），全部修改同样以 AGPL-3.0 开源。

本项目与《羊了个羊》官方无关，仅供学习与研究算法使用。

## 相对原项目的修改

依 AGPL-3.0 第 5 条说明：以下修改完成于 2026 年 10 月，建立在原项目 2024-12-29 的版本之上。

- **桌面应用与执行**：新增 Windows 桌面助手（`native/app`）、后台点击与逐步核对执行器（`native/clicker`）、断点续接与未确认点击复核。
- **自动抓取**：抓包服务的启停、代理与临时证书的自动恢复。
- **求解**：在原版回溯之外新增原生多路搜索（`native/solver`）、CP-SAT 整局约束求解、“一次移出”道具规划，以及消除后变色、电量与飞碟等特殊规则的精确回放。
- **棋盘识别**：截图与地图坐标的自动对齐、牌型模板学习。
- **原项目文件**：保留的原项目模块（`business/`、`core/`、`helper/FileHelper.py`）内容未改动，仅换行符不同。本项目不再使用的上游 CLI 入口（`solve.py`、`capture.py` 等）、联网与输入辅助模块（`GameLinkController`、`OnlineDataAnalyzer`、`InputHelper`、`ProjectHelper`、`RequestHelper`）和 `static/` 皮肤数据未随本仓库发布，需要时请从原项目获取。

## 目录

| 位置 | 内容 |
|---|---|
| `workflow_controller.py` | 常驻控制器（由 `羊羊助手.exe` 启动）：识别、规划器选择、状态、执行 |
| `static_plan.py` `static_accelerator.py` `capacity_probe.py` | 静态关总入口、原生多路搜索、容量诊断 |
| `prop_plan.py` `prop_search.py` | 一次移出道具规划与双回放验收 |
| `solve_constraints.py` `constraint_model.py` `constraint_conflicts.py` `tray_schedule.py` | CP-SAT 整局约束求解 |
| `standard_plan.py` `rolling_play.py` `frozen_replan.py` | 每日关 / 滚动规划 |
| `solve_verified_map.py` `solve_original_batch.py` `solve_energy_map.py` `original_*.py` `tail_search.py` `frontier_search.py` `dead_state_cache.py` `search_resources.py` | 求解后端与资源调度 |
| `color_rules.py` `energy_rules.py` | 特殊牌规则模型 |
| `capture_service.py` `auto_capture.py` `capture_runtime.py` `map_protocol.py` `game_known_plaintext.py` `game_crypto_probe.py` `map_acquire.py` `windows_user_ca.py` | 抓包服务、地图/种子解码 |
| `board_align.py` `board_watch.py` `app_bridge.py` `desktop_assistant.py` | 棋盘对齐、执行桥接、离线求解入口 |
| `business/` `core/` `helper/` | 上游原版 normal 算法（原版快照仍依赖） |
| `native/app` | 桌面应用 `SheepAutoApp.cs` |
| `native/clicker` | 点击器、答案执行器、结算检查、会话启动器 |
| `native/solver` | 原生搜索器；同一份源码生成 `FastOriginalSolver.exe` / `StaticFastSolver.exe` / `PropFastSolver.exe` |
| `native/planner` `native/rules` | 滚动规划器、电量搜索；变色 / 电量规则 |
| `tests/` | 单元测试 |
| `docs/` | 现行文档 |
| `_archive/` | 本地归档（不在仓库中）：已停用的旧版 exe、实验脚本、过期文档 |
| `output/` | 运行数据与历史会话（不进 git） |

编译出的 `*.exe` 放在项目根目录，Python 端按根目录查找，不要挪动。

## 常用命令（PowerShell，项目根目录）

```powershell
.\setup_windows.ps1                                       # 建 venv、装依赖
.\venv\Scripts\python.exe -m unittest discover -s tests   # 全部测试
.\build_clicker.ps1                                       # 构建全部 exe（含 PropFastSolver），并更新桌面快捷方式
.\build_prop_solver.ps1                                   # 只构建移出候选求解器
```

抓包开关：`启用自动抓取.bat` / `停止自动抓取.bat`；离线求解：`后台求解.bat`。
