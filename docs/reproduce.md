# 从公开仓库复现

## 环境和版本

- FPGA 工具：AMD Vivado/Vitis **2026.1**，器件 `xc7z020clg400-1`。
- 已验证板端环境：Python 3.10.4、NumPy 1.21.5、SciPy 1.8.0，PYNQ 与 OpenCV 由板卡系统提供。预装 Python 路径为 `/usr/local/share/pynq-venv/bin/python3`。
- Windows 原开发环境：Python 3.13，固定路径 `D:/Vivado2026.1/2026.1/tps/win64/python-3.13.0/python.exe`；Microsoft Edge 用于桌面应用显示。
- 源码快照：runtime13 / bootstrap9 / desktop12；硬件 ABI5。当前运行程序未因整理仓库而升级。

仓库中的 `source/` 使用小写分发路径，不能直接在其中启动原版程序。先恢复原文件名和目录，以保持 Python 导入和 Tcl 相对路径一致。

## 1. 校验并恢复独立目录

在 D 盘克隆仓库，然后执行：

```powershell
python -B tools/verify.py
python -B tools/materialize.py --output D:/pynq_reproduction_v1
python -B D:/pynq_reproduction_v1/verify.py
```

目标目录必须为空。清单包含每份原始源码的恢复位置、大小、SHA256；校验不连接设备，也不导入 PYNQ 驱动。

## 2. PC 整数参考和 RTL

```powershell
cd D:/pynq_reproduction_v1
python -B reproduce.py vectors
```

记录命令返回的 `logs/audio_filter_V7/rtl_vectors_.../result.json`。此步骤使用明确标记的合成数据及包内三段历史 PCM，逐项比较直接卷积、完整与子窗相关值。

```powershell
python -B reproduce.py simulate --proof <刚生成的vectors结果路径>
python -B reproduce.py build --proof <刚完成的simulate结果路径> --build-root D:/pynq_build_v1
python -B reproduce.py review --proof <刚完成的build目录中的summary.json>
```

`simulate` 调用 XSim，`build` 从 Tcl 生成 PS/PL 工程，`review` 检查时钟、约束、DRC、CDC、资源和生成文件。工具脚本使用固定 2026.1 路径；其他安装位置需在独立复现目录配置。这里不需要手工安装板卡仓库。构建目录选短的 ASCII 路径，避免 Windows 路径长度问题。

纯软件生命周期测试可在恢复目录运行：

```powershell
python -B scripts/test_av_phase_runtime_V13.py
```

该测试使用设备替身，不是板端实测。已有实现报告随包提供，发布整理没有重复构建 FPGA。

## 3. 新板卡上的配置

本步骤用于新的复现环境。已有服务运行的板子继续保留其安装；不能同时启动两套管理服务。

将仓库复制到板卡，例如 `~/pynq-av-tracker`，使用板端 Python 恢复到新目录：

```bash
/usr/local/share/pynq-venv/bin/python3 -B ~/pynq-av-tracker/tools/materialize.py --output /home/xilinx/pynq_tracker_v1
/usr/local/share/pynq-venv/bin/python3 -B ~/pynq-av-tracker/tools/prepare_board.py --workspace /home/xilinx/pynq_tracker_v1
```

`prepare_board.py` 只生成本机路径配置和 systemd 单元文件，检查源码与硬件 SHA，不启动程序。没有历史 boot、axis 或请求数据随配置安装。确认系统 Python、网络地址和新板连接后安装服务：

```bash
sudo cp /home/xilinx/pynq_tracker_v1/service_install/pynq-av-tracker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pynq-av-tracker.service
sudo systemctl status pynq-av-tracker.service
```

入口是原版 `av_service_bootstrap_V9.py`。管理器等待桌面服务请求，核对源码、共享锁和设备占用后启动 `av_phase_runtime_V13.py`。不依赖 Jupyter 父会话。首次使用载荷自带 `hardware/abi5/av_pmod.bit` 和匹配的 HWH；也可采用上一步通过完整审查的新构建，配套更新复现配置的文件摘要。

本次公开整理已验证配置生成及逐文件一致性，**没有在新 SD 卡上执行以上安装命令**；已有安装记录与新环境复现分开报告。

## 4. Windows 桌面

原开发机器可直接运行恢复目录的 `desktop_launcher_V12.py`。它使用已配置的 Python/Edge 路径并打开应用窗口。复制到另一台电脑时，当前默认扬声器身份不同，可使用发布包的适配入口：

```powershell
python -B tools/desktop_service.py --workspace D:/pynq_reproduction_v1 --out D:/pynq_reproduction_v1/logs/public_desktop
```

在 Edge 中打开 `http://127.0.0.1:8800/`，或用 Edge 的应用模式 `--app=http://127.0.0.1:8800/` 创建独立窗口。适配入口仅在内存中提供当前 Windows 默认音频设备身份，不编辑冻结源码。默认有限服务周期由原版桌面参数管理。

桌面会自动读取板端状态并发送幂等的服务启动请求。只有收到同 boot 的就绪信息，初始化按钮才可用。人工点击开始初始化后，保持电脑屏幕标识可见；第一宽带 WAV 的 SHA 为 `d835a32f414431e79a11d82fb785a483d84fe3e1b7f930ef85bfe26652903aea`，程序按板端参考阶段播放，结束后恢复音量。

电脑与板卡的单调时钟不能直接混算。时序、请求身份与文件写入完成时间分别保存；没有完成回执时不要重复发送同一次动作。

## 5. 看结果

以板端完成结果为准检查初始化状态、当前声音是否有效、返回图像的新旧和清理记录。初始化失败保留具体原因；常见原因包括图案遮挡、声源不匹配、子窗方位不一致和保护条件触发。音乐在阵列附近可听见，不意味着该段信号的时延必然满足质量条件。

历史成功记录和已知限制见 [验证说明](validation.md)。重新安装或改变相机、阵列的相对位置后，应由新触发重新建立相对坐标。
