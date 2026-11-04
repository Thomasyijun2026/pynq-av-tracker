# pynq-av-tracker

**基于 PYNQ-Z2 的声源定位与视觉跟随系统**

固定四麦阵列感知声音方向，旋转 USB 相机获取对应视野。PYNQ 在首次初始化中结合电脑发出的测试声与屏幕标识，建立声学方位和相机朝向的相对关系；之后由有效声音方向引导相机转向。声音计算、视觉判断与运动决策均在板端执行，桌面负责连接、人工触发和显示。

## 已实现的作品功能

| 功能 | 实现与展示 |
|---|---|
| 板端音频处理 | PL 完成 I2S 采集、16 抽头定点 FIR、六组时延相关及四子窗统计；PS 完成插值、几何拟合与质量判断 |
| 声学与视觉初始化 | 人工触发后执行全圈搜索，结合已知声音、屏幕标识和静止新图建立单声源相对映射 |
| 声音引导相机 | 实机演示展示手机位置变化、方位显示更新和相机支架转向 |
| 桌面直接连接 | 桌面自动连接 PYNQ、请求服务就绪；初始化由用户点击触发，后续通信由软件直接完成 |
| 可复现工程 | 提供 RTL、板端与桌面源码、构建脚本、测试向量、实现报告和工程恢复工具 |

展示场景为固定麦克风阵列、旋转相机与现场单声源。实机过程见[演示内容](docs/demo_recording.md)，完整设计见[技术报告](report/main.pdf)。

## 工程验证成果

- **FPGA 实现**：100 MHz，布线后建立裕量 **+0.287 ns**、保持裕量 **+0.035 ns**；LUT 14,822、FF 11,930、BRAM 1.5、DSP 2。
- **数字通路核对**：保存 PCM 与 PL 的 202 项完整统计、808 项子窗统计、6 个整窗峰、24 个子窗峰及质量字逐项比对。
- **仿真与归档复现**：两套 RTL 自检分别完成 14,598 和 731,932 项检查；提交源码归档独立解包后通过 43 项整数参考与 64 项运行生命周期检查。
- **优化与工程经验**：报告给出 FIR 流水化、板端相位计算和采样流程优化对照，并附大模型协作与可复用技能说明。

[验证说明](docs/validation.md)、[性能资料](docs/performance.md)和[原始结果索引](docs/request_results.md)保留各测试的输入、批次与验证范围，便于复核。

## 参赛成品

**演示视频、技术文档 PDF 和完整源码 RAR 已提交并冻结。** 本仓库的后续说明整理独立于这三份成品，运行源码保持提交时内容。文件身份与阅读顺序见[提交材料索引](docs/submitted_materials.md)。项目原创代码采用 [MIT](LICENSE)，外部组件见[第三方说明](docs/third_party.md)。

## 开始使用

1. 阅读[硬件与连接](docs/hardware.md)，准备 PYNQ-Z2、MIC01/05/07/08、UVC 相机和 ST3215 机构。
2. 按[复现说明](docs/reproduce.md)校验文件、恢复工程目录、配置板端服务和桌面入口。
3. 打开桌面，等待板端连接及定位服务就绪，再人工点击开始初始化，保持屏幕标识可见。
4. 初始化期间由电脑提供指定宽带声，板端完成搜索、静止确认及相对坐标建立；随后显示有效声音方向与相机图像。

```powershell
git clone https://github.com/Thomasyijun2026/pynq-av-tracker.git
cd pynq-av-tracker
python -B tools/verify.py
python -B tools/materialize.py --output D:/pynq_reproduction
```

仓库使用小写分发路径；`materialize.py` 根据清单恢复原来区分大小写的模块名和 Tcl 路径。该步骤只写入新的空目录，不连接板子。`README.md`、`LICENSE` 和 `SKILL.md` 保留工具约定名称。

## AMD 推荐目录对照

| 指南目录 | 本仓库入口 | 内容 |
|---|---|---|
| `src/` | [FPGA RTL、XDC 与工程脚本](source/fpga/)、[PYNQ 板端软件](source/software/pynq/)、[桌面端](source/desktop/) | PL、PS 与桌面源码 |
| `sim/` | [RTL 自检](source/fpga/audio_localize_v6/sim/)、[软件与整数参考测试](source/scripts/)、[验证说明](docs/validation.md) | 仿真、软件回归与结果 |
| `build/` | [复现入口](source/reproduce.py)、[Vivado Tcl](source/fpga/av_pmod_v7/tcl/)、[实现报告](validation/implementation/) | 构建、资源与时序 |
| `board/` | [硬件连接](docs/hardware.md)、[已实现上板文件](source/hardware/abi5/)、[安装与运行](docs/reproduce.md)、[初始化结果](validation/initialization_excerpt.json) | 上板工程、运行步骤和实测输出 |
| `data/` | [参考 PCM 与测试音](source/logs/av_standalone_v1/session_20261103_003121/)、[整数比对结果](validation/audio_reference.json)、[报告数据](validation/materials/) | 输入数据、参考结果和来源 |
| `skill/` | [技能包](skill/README.md) | 适用范围、使用方法、失败条件和验证案例 |
| `report/` | [技术报告 PDF](report/main.pdf)、[大模型协作记录](docs/ai_collaboration.md) | 设计报告、提示词、模型回答和纠错过程 |

[逐文件清单](manifests/) 记录冻结源码与发布文件的 SHA-256。[复现工具](tools/) 提供校验、目录恢复和板端配置生成。[架构与接口](docs/architecture.md) 说明 PL、PS 和桌面的分工。技术报告保留已提交原文；仓库技能包统一位于 [`skill/`](skill/README.md)。


评分项目与材料位置见[提交对应表](docs/submission.md)。
