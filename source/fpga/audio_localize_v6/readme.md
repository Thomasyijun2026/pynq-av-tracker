# Audio localization ABI5

The delivered RTL is audio_localize_V6 with av_pmod_V7 integration.
DOA ABI is 0x444f4105 and FIR_ID is 0x08040210.
The 16-tap FIR uses [1]*8 + [-1]*8, input >>4 and output >>2,
zero initial history and a two-FCLK-cycle registered commit.
The 32768-frame window exposes 202 full statistics and 4x202 subwindow
statistics, six full-window peaks, 24 subwindow peaks and a temporal guard.
Exact integer references and I2S/AXI lifecycle benches accompany the RTL.
Actual implementation reports and physical-validation limits are described
in the public repository docs/validation.md after source export.
