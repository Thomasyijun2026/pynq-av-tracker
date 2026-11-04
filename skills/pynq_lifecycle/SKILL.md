---
name: pynq-lifecycle
description: Preserve ownership, causal identity and cleanup evidence for bounded PYNQ peripheral operations.
---

# PYNQ 资源与请求管理

适用于多个入口共享音频 DMA、相机或 UART。输入是当前 boot、PID/start_ticks、请求 ID、模块摘要和拥有者记录。

1. 恢复前先查实际进程、共享锁、未完成请求与原结果。PC 部署进程结束不等于板端 worker 结束。
2. 在启动动作前保存请求意图。结果未知时读取原结果，不盲目重发。
3. 只有同 boot、源码身份、就绪文件和 HTTP 状态一致才能声明就绪。服务启动和初始化分开。
4. 结束时关闭 BCLK/WS，确认 DMA reset/halt 后释放缓冲，释放相机/UART，保存清理回执。
5. 不能确认清理时保留 UNKNOWN 状态；已有活动程序不被新的检查程序抢占。

验证包含过期身份、重复 ID、丢失响应、部分清理失败和启动异常。当前案例在 `av_service_bootstrap_V9.py`、`av_phase_runtime_V13.py` 及纯软件生命周期测试中保留。板端 `/proc`、文件锁和真实外设测试分别报告，设备替身不算上板验收。
