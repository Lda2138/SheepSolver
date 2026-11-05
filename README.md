# 羊羊助手（SheepSolver fork）

基于 NB-Dragon/SheepSolver（AGPL-3.0）。抓包取得地图与种子 → 识别棋盘 → 求解完整路线 → 后台点击执行。
当前支持：大世界静态关、普通牌“一次移出”规划及显式启动的执行流程、每日关 / 滚动规划；变色、电量规则作为求解规则的一部分保留。

最新进度与未完成事项见 `docs/交接文档-2026-10-31.md`。

## 点击与自动核对

在助手的 **设置 → 核对模式** 中选择，修改会在运行中生效并保存：

大世界若出现遮住棋盘的羊群特效，请在**游戏内齿轮 → 消除效果**关闭该开关。这类特效会实际盖住目标牌和牌槽，截图无法核对被遮住的内容；助手会明确提示特效遮挡，不把白色羊身当作新牌面。

- **逐步核对（默认）**：核对目标变化及牌槽结果，并等待稳定帧。
- **超高速**：确认本次目标牌离开后立即继续，不等牌槽动画；支持密集叠牌中露出的半遮挡灰牌。同图案叠牌等局部证据不足时，立即尝试目标变化及牌槽结果核对，不再额外等待稳定帧；目标被特效遮住时保留等待。
- **安全超高速**：采用超高速执行，每完成自选次数的点击自动暂停核对可见牌面、地图轮廓和牌槽。**核对间隔（次）** 支持 1–1000，默认 30；运行中也可修改。
- **全速**：起点和续接边界核对后按组连点：一组点到下一次三消为止，组内约隔 35 毫秒，只在组末核对一次；全部到位才点下一组。组内有点击未生效时原地重发。普通牌路线可用，电量关和变色关按单步执行。
- 超高速类模式普通点击至少间隔 135 毫秒，清牌后至少 150 毫秒；被客户端吞掉的点击在 500 毫秒后原地重发，无需退出复核。

加速模式开始、暂停后续接和模式切换时会自动加强核对；飞碟消牌和关卡收尾保留较严格的确认。全盘核对只能检查当前可见牌面与地图轮廓，遮住的牌仍依赖已验证地图。
平移游戏窗口会自动更新坐标并保留未确认点击，改变窗口尺寸则先重新核对，避免重复点击。速度与客户端动画、截图和牌型有关，不保证固定步频。

零标框等待时可复用本轮刚核对的目标截图；超过 50 毫秒仍重新截图。全速模式的标注预览最多每 250 毫秒更新一次，已确认动作和断点仍逐步保存。第 1 次点击未生效时也能按校准起点恢复。

牌槽逻辑计数按已验证路线和三消规则回放，已确认状态与待确认点击的预测状态分开记录，动画画面中的数量不会回写逻辑计数。超高速确认目标牌消失后即继续，不追加容量同步或开局全盘等待；安全超高速仍按自选点击次数做全盘核对。

## 一次移出执行入口

1. 找到一次移出路线后，主界面的绿色按钮为 **执行到手动移出**。
2. 点击后先核对当前局号、地图和断点，再自动执行到道具步骤。
3. 界面显示 **请手动使用一次移出**。到游戏内使用道具并完成广告，回助手点击 **已完成移出，核对并继续**；程序不会代点道具或广告。
4. 棋盘、牌槽和移出区匹配后自动继续，后续三张移出牌的点回由程序执行。识别不确定时保留断点并停止后续输入。支持暂停、停止和核对后续接。

该执行流程已通过带截图状态的客户端仿真；真实广告结束后的移出区布局与整局通关尚未实机验收。求解器产出的道具路线仍不会进入普通点击器，必须从上述专用入口启动。

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
| `development_archive/` | 历史源码、实验快照、旧版程序、示例数据与阶段性文档；仅供参考，见目录内说明 |
| `_archive/` | 本机原始归档（不在仓库中）；可公开的开发资料已整理到 `development_archive/` |
| `output/` | 运行数据与历史会话（不进 git） |

编译出的 `*.exe` 放在项目根目录，Python 端按根目录查找，不要挪动。

运行数据、抓包会话、本机证书和虚拟环境不包含在仓库中。首次使用请运行下方安装脚本，抓包证书由本机生成。历史记录不代表当前功能状态，也不能作为真实游戏通关的证明。

使用 `python map_acquire.py --inspect-cache` 检查微信缓存时，程序会自动查找唯一的用户小程序目录；有多个用户目录时，可通过 `--applet-dir <目录>` 显式指定。

## 常用命令（PowerShell，项目根目录）

```powershell
.\setup_windows.ps1                                       # 建 venv、装依赖
.\venv\Scripts\python.exe -m unittest discover -s tests   # 全部测试
.\build_clicker.ps1                                       # 构建全部 exe（含 PropFastSolver），并更新桌面快捷方式
.\build_prop_solver.ps1                                   # 只构建移出候选求解器
```

抓包开关：`启用自动抓取.bat` / `停止自动抓取.bat`；离线求解：`后台求解.bat`。
