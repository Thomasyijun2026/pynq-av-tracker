---
name: reproducible-delivery
description: Export an immutable FPGA and host-software snapshot with explicit source maps and independent archive readback.
---

# 可复现工程交付

适用于运行工程已经冻结、需要提供源码和复现包的场景。输入为实际安装版本、源码清单、硬件摘要和正式范围。

1. 先从实际安装清单取源，不用“文件名最新”的未安装候选替代。
2. 在独立目录复制程序，按文件记录大小和 SHA；新增文档与包装工具和运行程序分别标明。
3. 分发目录使用一致命名；大小写敏感的历史文件由映射恢复，避免修改导入和 Tcl 路径。
4. 对解包目录重新计算每个文件摘要、检查 Python 语法和链接，并执行有意义的离线复现。
5. 保存失败、版本和适用边界，明确实机数据、合成数据与包校验分别证明什么。

本工程曾遇到 Windows 长路径问题，使用 D 盘短构建路径后复现。发布检查由 `tools/verify.py` 执行；正式 RAR 要独立测试和解压逐文件比对，不能只查看压缩命令退出码。
