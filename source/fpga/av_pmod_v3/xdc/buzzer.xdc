# PMODA10 W19, TUL PYNQ-Z2 manual v1.1 Table13. Power/GND are not driven.
set_property PACKAGE_PIN W19 [get_ports buzzer_io]
set_property IOSTANDARD LVCMOS33 [get_ports buzzer_io]
set_property DRIVE 4 [get_ports buzzer_io]
set_property SLEW SLOW [get_ports buzzer_io]
set buzzer_meta [get_cells -hier -filter {NAME =~ */pad_meta_reg}]
set_false_path -from [get_ports buzzer_io] -to [get_pins -of_objects $buzzer_meta -filter {REF_PIN_NAME == D}]
# A slow asynchronous module control, bounded register-to-pad path. This is
# not a claim of timing against an external synchronous device.
set buzzer_drivers [get_cells -hier -filter {NAME =~ */buzzer_0/*/core/*_reg}]
set_max_delay 10.0 -datapath_only -from $buzzer_drivers -to [get_ports buzzer_io]
set_min_delay 0.0 -from $buzzer_drivers -to [get_ports buzzer_io]
