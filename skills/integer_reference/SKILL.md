---
name: integer-reference
description: Verify FPGA fixed-point statistics against an independent integer oracle and lifecycle scenarios.
---

# PL 整数参考与生命周期

适用于修改 FIR、相关器、快照或时窗接口。输入必须包括整数位宽、符号、算术移位、溢出规则、初始历史及样本中心。

1. 先冻结输入、系数和全部输出字段；用直接卷积/逐项求和建立与 RTL 实现结构不同的黄金参考。
2. 加入零值、满量程、符号反转、窗口边界、跳过帧、复位、暂停、重启和过近输入。
3. 同时比对全部统计值与质量字；只检查最终角度或峰位不够。
4. 仿真通过后检查实现时序、CDC、IO 和资源；负时序版本不得作为正式载荷。
5. 实机比较使用同一次保存的 PCM，记录 ABI、SHA 与清理状态。

失败时保留输入、首个不同字段和当前版本。禁止用浮点容差掩盖应当位精确的差异。本工程案例为 16 tap FIR、202 完整累加和 808 子窗统计；入口为恢复后的 `scripts/test_audio_filter_rtl_V7.py`。重复性不是绝对定位精度。
