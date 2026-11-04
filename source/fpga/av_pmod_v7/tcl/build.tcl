set source_root [file normalize [file join [file dirname [info script]] ..]]
set audio_root [file normalize [file join $source_root .. audio_localize_V6]]
set original_audio_root [file normalize [file join $source_root .. audio_localize_V2]]
set io_root [file normalize [file join $source_root .. av_pmod_V3]]
set buzzer_root [file normalize [file join $source_root .. buzzer_control_V1]]
set uart_root [file normalize [file join $source_root .. servo_uart_V1]]
set run_dir [file normalize [lindex $argv 0]]
set report_dir [file join $run_dir reports]
set out_dir [file join $run_dir output]
file mkdir $report_dir $out_dir
set_param general.maxThreads 2
proc require_equal {name actual expected} {if {$actual ne $expected} {error "$name expected=$expected actual=$actual"}}
create_project mic_debug $run_dir -part xc7z020clg400-1 -force
set_property target_language Verilog [current_project]
set_property simulator_language Mixed [current_project]
add_files [glob $audio_root/rtl/*.sv]
add_files [glob $uart_root/rtl/*.sv]
add_files [glob $io_root/rtl/*.sv]
add_files [glob $buzzer_root/rtl/*.sv]
add_files -fileset constrs_1 $original_audio_root/xdc/mic_debug.xdc
set_property PROCESSING_ORDER LATE [get_files mic_debug.xdc]
add_files -fileset constrs_1 $io_root/xdc/servo_uart.xdc
set_property PROCESSING_ORDER LATE [get_files servo_uart.xdc]
add_files -fileset constrs_1 $io_root/xdc/buttons.xdc
set_property PROCESSING_ORDER LATE [get_files buttons.xdc]
add_files -fileset constrs_1 $io_root/xdc/buzzer.xdc
set_property PROCESSING_ORDER LATE [get_files buzzer.xdc]
update_compile_order -fileset sources_1
create_bd_design system
set ps7 [create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 ps7]
source $original_audio_root/reference/pynq_ps_config_V1.tcl
set_property -dict $pynq_ps_config $ps7
set_property -dict [list CONFIG.PCW_USE_M_AXI_GP0 {1} CONFIG.PCW_USE_M_AXI_GP1 {0} \
 CONFIG.PCW_USE_S_AXI_GP0 {0} CONFIG.PCW_USE_S_AXI_GP1 {0} \
 CONFIG.PCW_USE_S_AXI_HP0 {1} CONFIG.PCW_USE_S_AXI_HP1 {0} CONFIG.PCW_USE_S_AXI_HP2 {0} CONFIG.PCW_USE_S_AXI_HP3 {0} \
 CONFIG.PCW_USE_S_AXI_ACP {0} CONFIG.PCW_USE_FABRIC_INTERRUPT {0} \
 CONFIG.PCW_GPIO_EMIO_GPIO_ENABLE {0} CONFIG.PCW_I2C1_PERIPHERAL_ENABLE {0} \
 CONFIG.PCW_EN_CLK0_PORT {1} CONFIG.PCW_EN_CLK1_PORT {0} CONFIG.PCW_EN_CLK2_PORT {0} CONFIG.PCW_EN_CLK3_PORT {0} \
 CONFIG.PCW_EN_RST0_PORT {1} CONFIG.PCW_EN_RST1_PORT {0} CONFIG.PCW_EN_RST2_PORT {0} CONFIG.PCW_EN_RST3_PORT {0} \
 CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ {100}] $ps7
make_bd_intf_pins_external [get_bd_intf_pins ps7/DDR]
set_property name DDR [get_bd_intf_ports DDR_0]
make_bd_intf_pins_external [get_bd_intf_pins ps7/FIXED_IO]
set_property name FIXED_IO [get_bd_intf_ports FIXED_IO_0]
set ctrl [create_bd_cell -type module -reference mic_capture_axi mic_capture_0]
set uart [create_bd_cell -type module -reference servo_uart_axi servo_uart_0]
set buttons [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 buttons_0]
set_property -dict [list CONFIG.C_GPIO_WIDTH {4} CONFIG.C_ALL_INPUTS {1} CONFIG.C_IS_DUAL {0} CONFIG.C_INTERRUPT_PRESENT {0}] $buttons
set btn_sync [create_bd_cell -type module -reference button_sync button_sync_0]
set buzzer_gpio [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 buzzer_gpio_0]
set_property -dict [list CONFIG.C_GPIO_WIDTH {32} CONFIG.C_ALL_OUTPUTS {1} CONFIG.C_DOUT_DEFAULT {0x00000000} CONFIG.C_IS_DUAL {1} CONFIG.C_GPIO2_WIDTH {32} CONFIG.C_ALL_INPUTS_2 {1} CONFIG.C_INTERRUPT_PRESENT {0}] $buzzer_gpio
set buzzer [create_bd_cell -type module -reference buzzer_pmod buzzer_0]
connect_bd_net [get_bd_pins buzzer_gpio_0/gpio_io_o] [get_bd_pins buzzer_0/command]
connect_bd_net [get_bd_pins buzzer_0/status] [get_bd_pins buzzer_gpio_0/gpio2_io_i]
create_bd_port -dir IO buzzer_io
connect_bd_net [get_bd_ports buzzer_io] [get_bd_pins buzzer_0/buzzer_io]
set dma [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_dma:7.1 axi_dma_0]
set_property -dict [list CONFIG.c_include_sg {0} CONFIG.c_include_mm2s {0} CONFIG.c_include_s2mm {1} \
 CONFIG.c_sg_length_width {26} CONFIG.c_m_axi_s2mm_data_width {64} CONFIG.c_s_axis_s2mm_tdata_width {32} \
 CONFIG.c_s2mm_burst_size {16} CONFIG.c_include_s2mm_dre {0}] $dma
set ctrl_ic [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 ctrl_interconnect]
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {5}] $ctrl_ic
set hp_ic [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 hp_interconnect]
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {1}] $hp_ic
set rst [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_pl]
set one [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 const_one]
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {1}] $one
set zero [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 const_zero]
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {0}] $zero
connect_bd_intf_net [get_bd_intf_pins ps7/M_AXI_GP0] [get_bd_intf_pins ctrl_interconnect/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins ctrl_interconnect/M00_AXI] [get_bd_intf_pins mic_capture_0/S_AXI]
connect_bd_intf_net [get_bd_intf_pins ctrl_interconnect/M01_AXI] [get_bd_intf_pins axi_dma_0/S_AXI_LITE]
connect_bd_intf_net [get_bd_intf_pins ctrl_interconnect/M02_AXI] [get_bd_intf_pins servo_uart_0/S_AXI]
connect_bd_intf_net [get_bd_intf_pins ctrl_interconnect/M03_AXI] [get_bd_intf_pins buttons_0/S_AXI]
connect_bd_intf_net [get_bd_intf_pins ctrl_interconnect/M04_AXI] [get_bd_intf_pins buzzer_gpio_0/S_AXI]
assign_bd_address -offset 0x43C30000 -range 64K -target_address_space [get_bd_addr_spaces ps7/Data] [get_bd_addr_segs buzzer_gpio_0/S_AXI/Reg] -force
connect_bd_net [get_bd_pins button_sync_0/btn_sync] [get_bd_pins buttons_0/gpio_io_i]
create_bd_port -dir I -from 3 -to 0 btn
connect_bd_net [get_bd_ports btn] [get_bd_pins button_sync_0/btn_async]
assign_bd_address -offset 0x43C20000 -range 64K -target_address_space [get_bd_addr_spaces ps7/Data] [get_bd_addr_segs buttons_0/S_AXI/Reg] -force
connect_bd_intf_net [get_bd_intf_pins mic_capture_0/M_AXIS] [get_bd_intf_pins axi_dma_0/S_AXIS_S2MM]
connect_bd_intf_net [get_bd_intf_pins axi_dma_0/M_AXI_S2MM] [get_bd_intf_pins hp_interconnect/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins hp_interconnect/M00_AXI] [get_bd_intf_pins ps7/S_AXI_HP0]
set clockpins [list ps7/M_AXI_GP0_ACLK ps7/S_AXI_HP0_ACLK rst_pl/slowest_sync_clk mic_capture_0/aclk servo_uart_0/aclk \
 axi_dma_0/s_axi_lite_aclk axi_dma_0/m_axi_s2mm_aclk \
 ctrl_interconnect/ACLK ctrl_interconnect/S00_ACLK ctrl_interconnect/M00_ACLK ctrl_interconnect/M01_ACLK ctrl_interconnect/M02_ACLK ctrl_interconnect/M03_ACLK buttons_0/s_axi_aclk button_sync_0/aclk ctrl_interconnect/M04_ACLK buzzer_gpio_0/s_axi_aclk buzzer_0/aclk \
 hp_interconnect/ACLK hp_interconnect/S00_ACLK hp_interconnect/M00_ACLK]
foreach p $clockpins {connect_bd_net [get_bd_pins ps7/FCLK_CLK0] [get_bd_pins $p]}
connect_bd_net [get_bd_pins ps7/FCLK_RESET0_N] [get_bd_pins rst_pl/ext_reset_in]
connect_bd_net [get_bd_pins const_one/dout] [get_bd_pins rst_pl/dcm_locked] [get_bd_pins rst_pl/aux_reset_in]
connect_bd_net [get_bd_pins const_zero/dout] [get_bd_pins rst_pl/mb_debug_sys_rst]
foreach p {ctrl_interconnect/ARESETN ctrl_interconnect/S00_ARESETN ctrl_interconnect/M00_ARESETN ctrl_interconnect/M01_ARESETN ctrl_interconnect/M02_ARESETN ctrl_interconnect/M03_ARESETN ctrl_interconnect/M04_ARESETN hp_interconnect/ARESETN hp_interconnect/S00_ARESETN hp_interconnect/M00_ARESETN} {
 connect_bd_net [get_bd_pins rst_pl/interconnect_aresetn] [get_bd_pins $p]
}
foreach p {buzzer_0/aresetn buzzer_gpio_0/s_axi_aresetn button_sync_0/aresetn buttons_0/s_axi_aresetn servo_uart_0/aresetn mic_capture_0/aresetn axi_dma_0/axi_resetn} {connect_bd_net [get_bd_pins rst_pl/peripheral_aresetn] [get_bd_pins $p]}
create_bd_port -dir O servo_tx
create_bd_port -dir I servo_rx
foreach p {servo_tx servo_rx} {connect_bd_net [get_bd_ports $p] [get_bd_pins servo_uart_0/$p]}
assign_bd_address -offset 0x43C10000 -range 64K -target_address_space [get_bd_addr_spaces ps7/Data] [get_bd_addr_segs servo_uart_0/S_AXI/reg0] -force
create_bd_port -dir O mic_bclk
create_bd_port -dir O mic_ws
create_bd_port -dir I -from 3 -to 0 mic_sd
foreach p {mic_bclk mic_ws mic_sd} {connect_bd_net [get_bd_ports $p] [get_bd_pins mic_capture_0/$p]}
assign_bd_address -offset 0x43C00000 -range 64K -target_address_space [get_bd_addr_spaces ps7/Data] [get_bd_addr_segs mic_capture_0/S_AXI/reg0] -force
assign_bd_address -offset 0x40400000 -range 64K -target_address_space [get_bd_addr_spaces ps7/Data] [get_bd_addr_segs axi_dma_0/S_AXI_LITE/Reg] -force
assign_bd_address -offset 0x00000000 -range 512M -target_address_space [get_bd_addr_spaces axi_dma_0/Data_S2MM] [get_bd_addr_segs ps7/S_AXI_HP0/HP0_DDR_LOWOCM] -force
validate_bd_design
require_equal reset_polarity [get_property CONFIG.C_EXT_RESET_HIGH $rst] 0
require_equal aux_reset_polarity [get_property CONFIG.C_AUX_RESET_HIGH $rst] 0
require_equal fclk_mhz [get_property CONFIG.PCW_ACT_FPGA0_PERIPHERAL_FREQMHZ $ps7] 100.000000
require_equal ddr_width [get_property CONFIG.PCW_UIPARAM_DDR_BUS_WIDTH $ps7] {16 Bit}
save_bd_design
write_bd_tcl -force $report_dir/generated_bd.tcl
set bd [get_files */system.bd]
set_property synth_checkpoint_mode None $bd
generate_target all $bd
add_files -norecurse [make_wrapper -files $bd -top]
set_property top system_wrapper [current_fileset]
update_compile_order -fileset sources_1
launch_runs synth_1 -jobs 2
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {error "Synthesis failed"}
set_property STEPS.PHYS_OPT_DESIGN.IS_ENABLED true [get_runs impl_1]
set_property STEPS.PHYS_OPT_DESIGN.ARGS.DIRECTIVE AggressiveExplore [get_runs impl_1]
set_property STEPS.ROUTE_DESIGN.ARGS.DIRECTIVE Explore [get_runs impl_1]
set_property STEPS.POST_ROUTE_PHYS_OPT_DESIGN.IS_ENABLED true [get_runs impl_1]
set_property STEPS.POST_ROUTE_PHYS_OPT_DESIGN.ARGS.DIRECTIVE AggressiveExplore [get_runs impl_1]
launch_runs impl_1 -to_step write_bitstream -jobs 2
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {error "Implementation failed"}
open_run impl_1
source $source_root/tcl/reports.tcl
file copy -force $run_dir/mic_debug.runs/impl_1/system_wrapper.bit $out_dir/av_pmod.bit
file copy -force $run_dir/mic_debug.gen/sources_1/bd/system/hw_handoff/system.hwh $out_dir/av_pmod.hwh
write_hw_platform -fixed -include_bit -force -file $out_dir/av_pmod.xsa
puts "AV_PMOD_AUDIO_V7_BUILD_PASS"
