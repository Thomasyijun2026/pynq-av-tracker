if {[llength [get_pins -hier -filter {REF_PIN_NAME == FCLKCLK[0]}]] != 1} {error "Expected one FCLK source"}
if {[llength [get_cells -hier -filter {NAME =~ */rx/sd_sample_reg*}]] != 4} {error "Expected four capture registers"}
report_utilization -file $report_dir/utilization.rpt
report_timing_summary -delay_type min_max -report_unconstrained -check_timing_verbose -file $report_dir/timing_summary.rpt
report_drc -file $report_dir/drc.rpt
report_methodology -file $report_dir/methodology.rpt
report_clocks -file $report_dir/clocks.rpt
report_clock_utilization -file $report_dir/clock_utilization.rpt
report_cdc -file $report_dir/cdc.rpt
report_exceptions -file $report_dir/exceptions.rpt
report_io -file $report_dir/io.rpt
check_timing -verbose -file $report_dir/check_timing.rpt
report_timing -from [get_ports {mic_sd[*]}] -delay_type min_max -max_paths 8 -path_type full_clock_expanded -file $report_dir/serial_input_timing.rpt
report_timing -to [get_ports mic_ws] -delay_type min_max -max_paths 2 -path_type full_clock_expanded -file $report_dir/ws_output_timing.rpt
set setup [get_timing_paths -delay_type max -max_paths 1]
set hold [get_timing_paths -delay_type min -max_paths 1]
if {![llength $setup] || ![llength $hold]} {error "No timed paths"}
set fp [open $report_dir/timing_metrics.tsv w]
puts $fp "setup_slack_ns\t[get_property SLACK $setup]"
puts $fp "hold_slack_ns\t[get_property SLACK $hold]"
puts $fp "clock_count\t[llength [get_clocks]]"
close $fp
if {[get_property SLACK $setup] < 0 || [get_property SLACK $hold] < 0} {error "Timing failed"}
set severe [get_drc_violations -quiet -filter {SEVERITY == Error || SEVERITY == {Critical Warning}}]
if {[llength $severe]} {error "Blocking DRC: $severe"}
foreach port {mic_bclk mic_ws {mic_sd[0]} {mic_sd[1]} {mic_sd[2]} {mic_sd[3]}} pin {W8 Y8 B19 W9 A20 Y9} {
 require_equal $port [get_property PACKAGE_PIN [get_ports $port]] $pin
 require_equal ${port}_standard [get_property IOSTANDARD [get_ports $port]] LVCMOS33
}
