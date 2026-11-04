# 第三方来源与许可

项目原创代码和本文档采用根目录 MIT License。第三方文件保留各自许可；MIT 不替代它们。

- PYNQ PS 配置及官方资料衍生约束：来自 Xilinx/PYNQ 项目和 TUL PYNQ-Z2 资料；原 BSD 许可保留于 [PYNQ 许可](../source/fpga/audio_localize_v2/reference/pynq_license_v1.txt)。原配置注释保留来源版本。
- AMD Vivado/Vitis 2026.1、预生成 bit/HWH/XSA 和 IP：工具和 IP 由 AMD 相应许可约束；本仓库不分发 Vivado/Vitis 安装包，不授予额外厂商许可。
- PYNQ 系统、Python、NumPy、SciPy、OpenCV、Microsoft Edge：外部环境依赖，不随本仓库分发。
- 第一宽带 WAV 与对应参考数组为项目测试资产，摘要见配置和清单。仓库没有分发曾用于排查的第二语音文件。
- 原始 PCM 输入仅用于整数参考复现，保存的是项目采集片段；它们没有真实方向标签，不能用来宣称绝对精度。

公开导出不包含浏览器资料、凭据、私有 Jupyter 传输助手、虚拟环境或工具安装副本。
