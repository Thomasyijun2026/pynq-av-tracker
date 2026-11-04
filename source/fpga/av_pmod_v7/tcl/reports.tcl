source $original_audio_root/tcl/reports.tcl
foreach port {servo_tx servo_rx} pin {W14 Y14} {
 require_equal $port [get_property PACKAGE_PIN [get_ports $port]] $pin
 require_equal ${port}_standard [get_property IOSTANDARD [get_ports $port]] LVCMOS33
}
foreach pattern {*/rx_meta_reg */rx_sync_reg} {
 set cells [get_cells -hier -filter "NAME =~ $pattern"]
 require_equal synchronizer_count [llength $cells] 1
 if {![get_property ASYNC_REG $cells]} {error "UART synchronizer missing ASYNC_REG"}
}
report_timing -to [get_ports servo_tx] -delay_type min_max -max_paths 2 -file $report_dir/uart_tx_timing.rpt
require_equal uart_tx_register_count [llength [get_cells -hier -filter {NAME =~ */servo_tx_reg}]] 1

foreach port {{btn[0]} {btn[1]} {btn[2]} {btn[3]}} pin {D19 D20 L20 L19} {
 require_equal $port [get_property PACKAGE_PIN [get_ports $port]] $pin
 require_equal ${port}_standard [get_property IOSTANDARD [get_ports $port]] LVCMOS33
}
foreach pattern {*/btn_meta_reg* */sync_stage_reg*} {
 set cells [get_cells -hier -filter "NAME =~ $pattern"]
 require_equal button_synchronizer_count [llength $cells] 4
 foreach c $cells {if {![get_property ASYNC_REG $c]} {error "Button synchronizer missing ASYNC_REG"}}
}
report_timing -from [get_cells -hier -filter {NAME =~ */btn_meta_reg*}] -to [get_cells -hier -filter {NAME =~ */sync_stage_reg*}] -max_paths 4 -file $report_dir/button_sync_timing.rpt

require_equal buzzer_pin [get_property PACKAGE_PIN [get_ports buzzer_io]] W19
require_equal buzzer_standard [get_property IOSTANDARD [get_ports buzzer_io]] LVCMOS33
foreach pattern {*/pad_meta_reg */pad_sync_reg} {
 set cells [get_cells -hier -filter "NAME =~ $pattern"]
 require_equal buzzer_synchronizer_count [llength $cells] 1
 if {![get_property ASYNC_REG $cells]} {error "Buzzer readback synchronizer missing"}
}
report_timing -to [get_ports buzzer_io] -delay_type min_max -max_paths 4 -file $report_dir/buzzer_output_timing.rpt
