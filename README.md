# pynq-av-tracker

**基于 PYNQ-Z2 的声视主动追踪系统**：四路麦克风提供声音方向，USB 相机寻找初始化标识，板端完成相对坐标对齐和相机朝向控制。电脑负责显示、已知测试声和人工触发，定位与视觉判断在 PYNQ 上执行。

本次公开版本冻结于 2026-11-04，包含已安装的 **板端运行程序 V13 / 服务入口 V9 / 桌面 V12 / FPGA ABI5**。运行源码按字节保留；发布整理没有改动现有设备程序。项目原创代码采用 [MIT](LICENSE)，外部组件见 [第三方说明](docs/third_party.md)。

## 开始使用

1. 阅读 [硬件与连接](docs/hardware.md)，准备 PYNQ-Z2、MIC01/05/07/08、UVC 相机和 ST3215 机构。
2. 按 [复现说明](docs/reproduce.md) 校验文件、恢复工程目录、配置板端服务和桌面入口。
3. 打开桌面界面后自动连接板子并请求定位服务就绪。连接本身不执行初始化。
4. 服务就绪后人工点击开始初始化，保持屏幕标识可见。电脑播放指定宽带声，板端完成扫描、静止确认及相对坐标建立。
5. 初始化成功后，板端使用当前有效声音方向更新相机视野；信号未通过质量判断时保留拒绝原因。

```powershell
git clone https://github.com/Thomasyijun2026/pynq-av-tracker.git
cd pynq-av-tracker
python -B tools/verify.py
python -B tools/materialize.py --output D:/pynq_reproduction_v1
```

仓库使用小写分发路径；`materialize.py` 根据清单恢复原来区分大小写的模块名和 Tcl 路径。该步骤只写入新的空目录，不连接板子。`README.md`、`LICENSE` 和 `SKILL.md` 保留工具约定名称。

## AMD 推荐目录对照

| 指南目录 | 本仓库入口 | 内容 |
|---|---|---|
| `src/` | [FPGA RTL、XDC 与工程脚本](source/fpga/)、[PYNQ 板端软件](source/software/pynq/)、[桌面端](source/desktop/) | PL、PS 与桌面源码 |
| `sim/` | [RTL 自检](source/fpga/audio_localize_v6/sim/)、[软件与整数参考测试](source/scripts/)、[验证说明](docs/validation.md) | 仿真、软件回归与结果 |
| `build/` | [复现入口](source/reproduce.py)、[Vivado Tcl](source/fpga/av_pmod_v7/tcl/)、[实现报告](validation/implementation/) | 构建、资源与时序 |
| `board/` | [硬件连接](docs/hardware.md)、[ABI5 上板文件](source/hardware/abi5/)、[安装与运行](docs/reproduce.md)、[历史初始化结果](validation/initialization_excerpt.json) | 上板工程、运行步骤和实测输出 |
| `data/` | [参考 PCM 与测试音](source/logs/av_standalone_v1/session_20261103_003121/)、[整数比对结果](validation/audio_reference.json)、[报告数据](validation/materials/) | 输入数据、参考结果和来源 |
| `skill/` | [技能包](skill/README.md) | 适用范围、使用方法、失败条件和验证案例 |
| `report/` | [技术报告 PDF](report/main.pdf)、[大模型协作记录](docs/ai_collaboration.md) | 设计报告、提示词、模型回答和纠错过程 |

[逐文件清单](manifests/) 记录冻结源码与发布文件的 SHA-256。[复现工具](tools/) 提供校验、目录恢复和板端配置生成。[架构与接口](docs/architecture.md) 说明 PL、PS 和桌面的分工。技术报告保留冻结稿原文，其中的 `skills/` 对应当前的 [`skill/`](skill/README.md)。

## 已有验证

ABI5 的历史实现审查通过：100 MHz、LUT 14822、FF 11930、BRAM 1.5、DSP 2；布线后 setup slack 为 **+0.287 ns**，hold slack 为 **+0.035 ns**。历史 XSim 两套测试分别完成 14598 和 731932 项检查。RTL 对保存 PCM 的完整相关值、子窗相关值、峰值和质量字均进行整数比对。详见 [验证说明](docs/validation.md)。

已保存实机初始化成功记录和声音驱动新画面的记录，也保留了声源确认、方向一致性及保护条件拒绝的记录。软件目前按用户确认的现有版本冻结。**公开包校验、仿真通过和重复性数据分别报告；绝对定位精度、新 SD 卡部署及更多独立声源场景尚待验证。** 新部署应使用本次安装的初始化结果，不使用旧运行的坐标映射。

本仓库提供完整工程与材料入口；真实演示视频、官方签署文件和参赛系统上传由提交者补齐。评分项目与证据位置见 [提交对应表](docs/submission.md)。
