`timescale 1ns/1ps
// 100 MHz, 1 Mbaud 8N1; no interrupts, DMA, motion commands or clock generation.
module servo_uart_axi (
    (* X_INTERFACE_INFO="xilinx.com:signal:clock:1.0 aclk CLK", X_INTERFACE_PARAMETER="ASSOCIATED_BUSIF S_AXI, ASSOCIATED_RESET aresetn, FREQ_HZ 100000000" *) input wire aclk,
    (* X_INTERFACE_INFO="xilinx.com:signal:reset:1.0 aresetn RST", X_INTERFACE_PARAMETER="POLARITY ACTIVE_LOW" *) input wire aresetn,
    (* X_INTERFACE_INFO="xilinx.com:interface:aximm:1.0 S_AXI AWADDR", X_INTERFACE_PARAMETER="PROTOCOL AXI4LITE, DATA_WIDTH 32, ADDR_WIDTH 8, FREQ_HZ 100000000" *) input wire [7:0] s_axi_awaddr,
    (* X_INTERFACE_INFO="xilinx.com:interface:aximm:1.0 S_AXI AWPROT" *) input wire [2:0] s_axi_awprot,
    (* X_INTERFACE_INFO="xilinx.com:interface:aximm:1.0 S_AXI AWVALID" *) input wire s_axi_awvalid,
    (* X_INTERFACE_INFO="xilinx.com:interface:aximm:1.0 S_AXI AWREADY" *) output wire s_axi_awready,
    (* X_INTERFACE_INFO="xilinx.com:interface:aximm:1.0 S_AXI WDATA" *) input wire [31:0] s_axi_wdata,
    (* X_INTERFACE_INFO="xilinx.com:interface:aximm:1.0 S_AXI WSTRB" *) input wire [3:0] s_axi_wstrb,
    (* X_INTERFACE_INFO="xilinx.com:interface:aximm:1.0 S_AXI WVALID" *) input wire s_axi_wvalid,
    (* X_INTERFACE_INFO="xilinx.com:interface:aximm:1.0 S_AXI WREADY" *) output wire s_axi_wready,
    (* X_INTERFACE_INFO="xilinx.com:interface:aximm:1.0 S_AXI BRESP" *) output reg [1:0] s_axi_bresp,
    (* X_INTERFACE_INFO="xilinx.com:interface:aximm:1.0 S_AXI BVALID" *) output reg s_axi_bvalid,
    (* X_INTERFACE_INFO="xilinx.com:interface:aximm:1.0 S_AXI BREADY" *) input wire s_axi_bready,
    (* X_INTERFACE_INFO="xilinx.com:interface:aximm:1.0 S_AXI ARADDR" *) input wire [7:0] s_axi_araddr,
    (* X_INTERFACE_INFO="xilinx.com:interface:aximm:1.0 S_AXI ARPROT" *) input wire [2:0] s_axi_arprot,
    (* X_INTERFACE_INFO="xilinx.com:interface:aximm:1.0 S_AXI ARVALID" *) input wire s_axi_arvalid,
    (* X_INTERFACE_INFO="xilinx.com:interface:aximm:1.0 S_AXI ARREADY" *) output wire s_axi_arready,
    (* X_INTERFACE_INFO="xilinx.com:interface:aximm:1.0 S_AXI RDATA" *) output reg [31:0] s_axi_rdata,
    (* X_INTERFACE_INFO="xilinx.com:interface:aximm:1.0 S_AXI RRESP" *) output reg [1:0] s_axi_rresp,
    (* X_INTERFACE_INFO="xilinx.com:interface:aximm:1.0 S_AXI RVALID" *) output reg s_axi_rvalid,
    (* X_INTERFACE_INFO="xilinx.com:interface:aximm:1.0 S_AXI RREADY" *) input wire s_axi_rready,
    input wire servo_rx,
    output reg servo_tx
);
    localparam integer BIT_CYCLES=100, DEPTH=256;
    (* ASYNC_REG="TRUE" *) reg rx_meta, rx_sync;
    always @(posedge aclk) begin
        if (!aresetn) begin rx_meta<=1; rx_sync<=1; end
        else begin rx_meta<=servo_rx; rx_sync<=rx_meta; end
    end
    reg [1:0] enable;
    reg aw_pending,w_pending;
    reg [7:0] aw_address;
    reg [31:0] w_data;
    reg [3:0] w_strobes;
    assign s_axi_awready=!aw_pending && !s_axi_bvalid;
    assign s_axi_wready=!w_pending && !s_axi_bvalid;
    assign s_axi_arready=!s_axi_rvalid;
    wire wr=aw_pending && w_pending && !s_axi_bvalid;
    wire rd=s_axi_arvalid && s_axi_arready;
    wire ctrl=wr && aw_address==8'h04;
    wire flush=ctrl && w_strobes[1] && w_data[8];
    wire tx_req=wr && aw_address==8'h0c && w_strobes[0];
    (* ram_style="distributed" *) reg [7:0] tx_fifo[0:255];
    (* ram_style="distributed" *) reg [7:0] rx_fifo[0:255];
    reg [7:0] tx_wp,tx_rp,rx_wp,rx_rp;
    reg [8:0] tx_count,rx_count;
    wire rx_pop=rd && s_axi_araddr==8'h0c && rx_count!=0;
    reg [3:0] tx_phase,rx_phase;
    reg [6:0] tx_timer,rx_timer;
    reg [7:0] tx_shift,rx_shift;
    reg [2:0] rx_bit;
    reg rx_overflow,frame_error,tx_overflow;
    wire tx_pop=enable[1] && tx_phase==0 && tx_count!=0;
    wire tx_push=tx_req && enable[1] && (tx_count<DEPTH || tx_pop);
    wire rx_byte=enable[0] && rx_phase==3 && rx_timer==0 && rx_sync;
    wire rx_push=rx_byte && (rx_count<DEPTH || rx_pop);

    // Independent AW/W, stable B/R during backpressure, no read side effect twice.
    always @(posedge aclk) begin
        if (!aresetn) begin
            aw_pending<=0; w_pending<=0; aw_address<=0; w_data<=0; w_strobes<=0;
            s_axi_bvalid<=0; s_axi_bresp<=0; s_axi_rvalid<=0; s_axi_rresp<=0; s_axi_rdata<=0;
            enable<=0;
        end else begin
            if(s_axi_awvalid && s_axi_awready) begin aw_pending<=1; aw_address<=s_axi_awaddr; end
            if(s_axi_wvalid && s_axi_wready) begin w_pending<=1; w_data<=s_axi_wdata; w_strobes<=s_axi_wstrb; end
            if(s_axi_bvalid && s_axi_bready) s_axi_bvalid<=0;
            if(wr) begin
                aw_pending<=0; w_pending<=0; s_axi_bvalid<=1; s_axi_bresp<=0;
                case(aw_address)
                    8'h04: if(w_strobes[0]) enable<=w_data[1:0];
                    8'h0c: if(w_strobes[0] && !tx_push) s_axi_bresp<=2;
                    default: s_axi_bresp<=2;
                endcase
            end
            if(s_axi_rvalid && s_axi_rready) s_axi_rvalid<=0;
            if(rd) begin
                s_axi_rvalid<=1; s_axi_rresp<=0; s_axi_rdata<=0;
                case(s_axi_araddr)
                    8'h00: s_axi_rdata<=32'h55525431;
                    8'h04: s_axi_rdata<={30'd0,enable};
                    8'h08: s_axi_rdata<={25'd0,(rx_phase!=0),tx_overflow,frame_error,rx_overflow,(tx_phase!=0),(tx_count<DEPTH),(rx_count!=0)};
                    8'h0c: if(rx_count!=0) s_axi_rdata<={24'd0,rx_fifo[rx_rp]}; else s_axi_rresp<=2;
                    8'h10: s_axi_rdata<={7'd0,tx_count,7'd0,rx_count};
                    8'h14: s_axi_rdata<=BIT_CYCLES;
                    default: s_axi_rresp<=2;
                endcase
            end
        end
    end
    // Flush aborts both directions and clears sticky faults; default TX is idle high.
    always @(posedge aclk) begin
        if(!aresetn || flush) begin
            tx_wp<=0;tx_rp<=0;rx_wp<=0;rx_rp<=0;tx_count<=0;rx_count<=0;
            tx_phase<=0;rx_phase<=0;tx_timer<=0;rx_timer<=0;tx_shift<=0;rx_shift<=0;rx_bit<=0;
            servo_tx<=1;rx_overflow<=0;frame_error<=0;tx_overflow<=0;
        end else begin
            if(tx_push) begin tx_fifo[tx_wp]<=w_data[7:0];tx_wp<=tx_wp+1'b1;end
            if(tx_pop) tx_rp<=tx_rp+1'b1;
            case({tx_push,tx_pop}) 2'b10:tx_count<=tx_count+1'b1;2'b01:tx_count<=tx_count-1'b1;default:;endcase
            if(tx_req && !tx_push) tx_overflow<=1;
            if(rx_push) begin rx_fifo[rx_wp]<=rx_shift;rx_wp<=rx_wp+1'b1;end
            if(rx_pop) rx_rp<=rx_rp+1'b1;
            case({rx_push,rx_pop}) 2'b10:rx_count<=rx_count+1'b1;2'b01:rx_count<=rx_count-1'b1;default:;endcase
            if(rx_byte && !rx_push) rx_overflow<=1;
            if(!enable[1]) begin tx_phase<=0;servo_tx<=1;end
            else if(tx_phase==0) begin
                if(tx_pop) begin tx_shift<=tx_fifo[tx_rp];servo_tx<=0;tx_phase<=1;tx_timer<=BIT_CYCLES-1;end
            end else if(tx_timer!=0) tx_timer<=tx_timer-1'b1;
            else begin
                tx_timer<=BIT_CYCLES-1;
                if(tx_phase<=8) begin servo_tx<=tx_shift[0];tx_shift<={1'b0,tx_shift[7:1]};tx_phase<=tx_phase+1'b1;end
                else if(tx_phase==9) begin servo_tx<=1;tx_phase<=10;end
                else begin servo_tx<=1;tx_phase<=0;end
            end
            if(!enable[0]) rx_phase<=0;
            else case(rx_phase)
                0: if(!rx_sync) begin rx_phase<=1;rx_timer<=BIT_CYCLES/2-1;end
                1: if(rx_timer!=0) rx_timer<=rx_timer-1'b1;
                   else if(rx_sync) rx_phase<=0;
                   else begin rx_phase<=2;rx_bit<=0;rx_timer<=BIT_CYCLES-1;end
                2: if(rx_timer!=0) rx_timer<=rx_timer-1'b1;
                   else begin rx_shift[rx_bit]<=rx_sync;rx_timer<=BIT_CYCLES-1;
                       if(rx_bit==7) rx_phase<=3;else rx_bit<=rx_bit+1'b1;end
                3: if(rx_timer!=0) rx_timer<=rx_timer-1'b1;
                   else if(rx_sync) rx_phase<=0;
                   else begin frame_error<=1;rx_phase<=4;end
                4: if(rx_sync) rx_phase<=0; // break: wait high, never invent repeated bytes
                default:rx_phase<=0;
            endcase
        end
    end
endmodule
