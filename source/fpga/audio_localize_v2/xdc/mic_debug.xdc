# PYNQ-Z2 40-pin Raspberry Pi header. Physical pins 33,35,36,37,38,40.
# Verified against TUL manual v1.1 p27, schematic R12 sheet4/11 and master XDC.
# See ../wiring.csv. Ports are LVCMOS33; header physical pins are not GPIO numbers.
set_property PACKAGE_PIN W8 [get_ports mic_bclk]
set_property PACKAGE_PIN Y8 [get_ports mic_ws]
set_property PACKAGE_PIN B19 [get_ports {mic_sd[0]}]
set_property PACKAGE_PIN W9 [get_ports {mic_sd[1]}]
set_property PACKAGE_PIN A20 [get_ports {mic_sd[2]}]
set_property PACKAGE_PIN Y9 [get_ports {mic_sd[3]}]
set_property IOSTANDARD LVCMOS33 [get_ports {mic_bclk mic_ws mic_sd[*]}]
set_property SLEW SLOW [get_ports {mic_bclk mic_ws}]
set_property DRIVE 8 [get_ports {mic_bclk mic_ws}]
# Supplementary weak pull-down only. Physical shared-line tri-state/pull-downs
# still require verification; this does not identify or qualify the modules.
set_property PULLDOWN true [get_ports {mic_sd[*]}]

# BCLK is a forwarded divided clock; all internal registers use FCLK0.
set fclk_pin [get_pins -hier -filter {REF_PIN_NAME == FCLKCLK[0]}]
create_generated_clock -name mic_bclk -source $fclk_pin -divide_by 50 [get_ports mic_bclk]
# NXP UM11732: max transmitter delay 0.8*T = 400ns at 2MHz, plus 20ns
# unmeasured roundtrip/skew budget. This is a design assumption, not a clone spec.
set_input_delay -clock mic_bclk -max 420.0 [get_ports {mic_sd[*]}]
set_input_delay -clock mic_bclk -min 0.0 [get_ports {mic_sd[*]}]
set sd_capture [get_cells -hier -filter {NAME =~ */rx/sd_sample_reg*}]
set_multicycle_path 50 -setup -end -from [get_ports {mic_sd[*]}] -to $sd_capture
set_multicycle_path 49 -hold -end -from [get_ports {mic_sd[*]}] -to $sd_capture

# WS updates only at falling BCLK, 25 FCLK cycles from either rising edge.
# Max 20ns accounts for output flight/skew; min -40ns = 20ns INMP441 hold
# plus 20ns flight/skew. Scoped source-edge shifts model the CE phase.
set_output_delay -clock mic_bclk -max 20.0 [get_ports mic_ws]
set_output_delay -clock mic_bclk -min -40.0 [get_ports mic_ws]
set_multicycle_path 25 -setup -start -to [get_ports mic_ws]
set_multicycle_path 49 -hold -start -to [get_ports mic_ws]
