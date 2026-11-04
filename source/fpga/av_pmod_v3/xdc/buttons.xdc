# Source: fpga/xdc/pynq_z2_master_template_V1.xdc and TUL manual v1.1 table9.
set_property PACKAGE_PIN D19 [get_ports {btn[0]}]
set_property PACKAGE_PIN D20 [get_ports {btn[1]}]
set_property PACKAGE_PIN L20 [get_ports {btn[2]}]
set_property PACKAGE_PIN L19 [get_ports {btn[3]}]
set_property IOSTANDARD LVCMOS33 [get_ports {btn[*]}]
# External buttons have no clock relationship. Only first-stage D is excepted.
set button_meta [get_cells -hier -filter {NAME =~ */btn_meta_reg*}]
set_false_path -from [get_ports {btn[*]}] -to [get_pins -of_objects $button_meta -filter {REF_PIN_NAME == D}]
