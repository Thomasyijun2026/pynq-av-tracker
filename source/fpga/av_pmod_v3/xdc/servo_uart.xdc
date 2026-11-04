# PYNQ-Z2 PMODB1/2: official TUL manual and schematic.
set_property PACKAGE_PIN W14 [get_ports servo_tx]
set_property PACKAGE_PIN Y14 [get_ports servo_rx]
set_property IOSTANDARD LVCMOS33 [get_ports {servo_tx servo_rx}]
set_property DRIVE 8 [get_ports servo_tx]
set_property SLEW SLOW [get_ports servo_tx]
set_property PULLUP true [get_ports servo_rx]
# UART has no external clock phase relationship. Only the first synchronizer
# endpoint is exempt; rx_meta -> rx_sync remains timed at 100 MHz.
set uart_meta [get_cells -hier -filter {NAME =~ */rx_meta_reg}]
set_false_path -from [get_ports servo_rx] -to [get_pins -of_objects $uart_meta -filter {REF_PIN_NAME == D}]
# Bound internal TX register-to-pad delay; this is not a synchronous receiver
# setup/hold claim. 10ns is 1% of the 1us serial bit period.
set uart_tx_reg [get_cells -hier -filter {NAME =~ */servo_tx_reg}]
set_max_delay 10.0 -datapath_only -from $uart_tx_reg -to [get_ports servo_tx]
set_min_delay 0.0 -from $uart_tx_reg -to [get_ports servo_tx]
