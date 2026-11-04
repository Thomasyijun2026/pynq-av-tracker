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

## 目录

| 目录 | 内容 |
|---|---|
| [source](source/) | 原版本 PS Python、桌面 HTML/JS、RTL、XDC、Tcl、仿真测试、预生成 bit/HWH/XSA、整数参考输入 |
| [docs](docs/) | 结构、算法、硬件、从零复现、验证边界、官方交付对应关系 |
| [skills](skills/README.md) | 可复用工程技能、执行步骤、失败处理和已验证案例 |
| [validation](validation/) | 实现报告、音频数字比对、初始化结果节选、发布检查 |
| [manifests](manifests/) | 源码路径映射、逐文件大小和 SHA256 |
| [tools](tools/) | 只读校验、目录恢复、安装配置生成、桌面适配入口 |

## 已有验证

ABI5 的历史实现审查通过：100 MHz、LUT 14822、FF 11930、BRAM 1.5、DSP 2；布线后 setup slack 为 **+0.287 ns**，hold slack 为 **+0.035 ns**。历史 XSim 两套测试分别完成 14598 和 731932 项检查。RTL 对保存 PCM 的完整相关值、子窗相关值、峰值和质量字均进行整数比对。详见 [验证说明](docs/validation.md)。

已保存实机初始化成功记录和声音驱动新画面的记录，也保留了声源确认、方向一致性及保护条件拒绝的记录。软件目前按用户确认的现有版本冻结。**公开包校验、仿真通过和重复性数据分别报告；绝对定位精度、新 SD 卡部署及更多独立声源场景尚待验证。** 新部署应使用本次安装的初始化结果，不使用旧运行的坐标映射。

本仓库提供完整工程与材料入口；真实演示视频、官方签署文件和参赛系统上传由提交者补齐。评分项目与证据位置见 [提交对应表](docs/submission.md)。
