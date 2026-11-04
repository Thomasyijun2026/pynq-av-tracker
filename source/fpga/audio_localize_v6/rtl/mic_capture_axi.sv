`timescale 1ns/1ps
module mic_capture_axi #(
    parameter integer WARMUP_FRAMES = 4096,
    parameter integer FIFO_LOG2 = 4,
    parameter integer DOA_WINDOW_FRAMES = 32768,
    parameter integer DOA_SKIP_FRAMES = 31250
) (
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 aclk CLK", X_INTERFACE_PARAMETER = "ASSOCIATED_BUSIF S_AXI:M_AXIS, ASSOCIATED_RESET aresetn, FREQ_HZ 100000000" *)
    input wire aclk,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 aresetn RST", X_INTERFACE_PARAMETER = "POLARITY ACTIVE_LOW" *)
    input wire aresetn,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWADDR", X_INTERFACE_PARAMETER = "PROTOCOL AXI4LITE, DATA_WIDTH 32, ADDR_WIDTH 8, FREQ_HZ 100000000" *) input wire [7:0] s_axi_awaddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWPROT" *) input wire [2:0] s_axi_awprot,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWVALID" *) input wire s_axi_awvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWREADY" *) output wire s_axi_awready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WDATA" *) input wire [31:0] s_axi_wdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WSTRB" *) input wire [3:0] s_axi_wstrb,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WVALID" *) input wire s_axi_wvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WREADY" *) output wire s_axi_wready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BRESP" *) output reg [1:0] s_axi_bresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BVALID" *) output reg s_axi_bvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BREADY" *) input wire s_axi_bready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARADDR" *) input wire [7:0] s_axi_araddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARPROT" *) input wire [2:0] s_axi_arprot,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARVALID" *) input wire s_axi_arvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARREADY" *) output wire s_axi_arready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RDATA" *) output reg [31:0] s_axi_rdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RRESP" *) output reg [1:0] s_axi_rresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RVALID" *) output reg s_axi_rvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RREADY" *) input wire s_axi_rready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TDATA" *) output wire [31:0] m_axis_tdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TKEEP" *) output wire [3:0] m_axis_tkeep,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TLAST" *) output wire m_axis_tlast,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TVALID" *) output wire m_axis_tvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TREADY" *) input wire m_axis_tready,
    input wire [3:0] mic_sd,
    output wire mic_bclk, mic_ws
);
    localparam integer DEPTH = 1 << FIFO_LOG2;
    reg clock_enable, busy, collecting, done, overflow, aborted, config_error;
    reg [31:0] requested_frames, captured_frames, sent_frames, warm_frames;
    wire frame_valid;
    wire [255:0] frame_data;
    i2s_rx rx(.clk(aclk), .resetn(aresetn), .enable(clock_enable),
        .mic_sd(mic_sd), .mic_bclk(mic_bclk), .mic_ws(mic_ws),
        .frame_valid(frame_valid), .frame_data(frame_data));
    (* ram_style = "distributed" *) reg [255:0] fifo_data [0:DEPTH-1];
    reg [DEPTH-1:0] fifo_last;
    reg [FIFO_LOG2-1:0] wr_ptr, rd_ptr;
    reg [FIFO_LOG2:0] fifo_count;
    reg [2:0] channel_index;
    wire warm_ready = (warm_frames >= WARMUP_FRAMES);
    assign m_axis_tvalid = (fifo_count != 0);
    assign m_axis_tdata = fifo_data[rd_ptr][channel_index*32 +: 32];
    assign m_axis_tkeep = 4'hf;
    assign m_axis_tlast = fifo_last[rd_ptr] && (channel_index == 7) && m_axis_tvalid;
    wire beat_taken = m_axis_tvalid && m_axis_tready;
    wire pop_frame = beat_taken && (channel_index == 7);
    wire want_frame = frame_valid && collecting && warm_ready;
    wire push_frame = want_frame && ((fifo_count < DEPTH) || pop_frame);

    // AW and W are independently buffered. One outstanding read and write.
    reg aw_pending, w_pending;
    reg [7:0] aw_address;
    reg [31:0] w_data;
    reg [3:0] w_strobes;
    assign s_axi_awready = !aw_pending && !s_axi_bvalid;
    assign s_axi_wready = !w_pending && !s_axi_bvalid;
    assign s_axi_arready = !s_axi_rvalid;
    wire write_fire = aw_pending && w_pending && !s_axi_bvalid;
    wire control_write = write_fire && (aw_address == 8'h04) && w_strobes[0];
    wire abort_cmd = control_write && (w_data[2] || !w_data[0]);
    wire start_cmd = control_write && w_data[1] && w_data[0] && !w_data[2];
    wire valid_length = (requested_frames != 0) && (requested_frames <= 32'h001fffff);

    reg [9:0] diagnostic_index;
    wire [63:0] diagnostic_data;
    reg [7:0] doa_read_index;
    wire [63:0] doa_read_data;
    wire [47:0] doa_peak_lags;
    wire [191:0] doa_subwindow_peaks;
    wire [3:0] doa_temporal_mask;
    wire [2:0] doa_temporal_support;
    wire doa_temporal_stable;
    wire doa_ready,doa_active,doa_overrun;
    wire [31:0] doa_frames;
    doa_correlator #(.WINDOW_FRAMES(DOA_WINDOW_FRAMES),.SKIP_FRAMES(DOA_SKIP_FRAMES)) doa(
        .clk(aclk),.resetn(aresetn),.arm(start_cmd && !busy && valid_length),.abort_capture(abort_cmd),
        .sample_valid(push_frame),.frame_data(frame_data),.read_index(doa_read_index),.read_data(doa_read_data),
        .diagnostic_index(diagnostic_index),.diagnostic_data(diagnostic_data),
        .ready(doa_ready),.active(doa_active),.overrun(doa_overrun),.accumulated_frames(doa_frames),.peak_lags(doa_peak_lags),
        .subwindow_peaks(doa_subwindow_peaks),.temporal_match_mask(doa_temporal_mask),
        .temporal_support(doa_temporal_support),.temporal_stable(doa_temporal_stable));

    function automatic [31:0] strobed(input [31:0] oldval, input [31:0] newval, input [3:0] st);
        integer k;
        begin
            strobed = oldval;
            for(k=0;k<4;k=k+1) if(st[k]) strobed[k*8 +: 8] = newval[k*8 +: 8];
        end
    endfunction

    always @(posedge aclk) begin
        if (!aresetn) begin
            aw_pending <= 0; w_pending <= 0; aw_address <= 0; w_data <= 0; w_strobes <= 0;
            s_axi_bvalid <= 0; s_axi_bresp <= 0;
            s_axi_rvalid <= 0; s_axi_rdata <= 0; s_axi_rresp <= 0;
            requested_frames <= 31250;
            doa_read_index <= 0;diagnostic_index<=0;
            clock_enable <= 0; busy <= 0; collecting <= 0; done <= 0;
            overflow <= 0; aborted <= 0; config_error <= 0;
            captured_frames <= 0; sent_frames <= 0; warm_frames <= 0;
            wr_ptr <= 0; rd_ptr <= 0; fifo_count <= 0; fifo_last <= 0; channel_index <= 0;
        end else begin
            if (!clock_enable) warm_frames <= 0;
            else if (frame_valid && !warm_ready) warm_frames <= warm_frames + 1'b1;
            if (s_axi_awvalid && s_axi_awready) begin aw_address <= s_axi_awaddr; aw_pending <= 1; end
            if (s_axi_wvalid && s_axi_wready) begin w_data <= s_axi_wdata; w_strobes <= s_axi_wstrb; w_pending <= 1; end
            if (s_axi_bvalid && s_axi_bready) s_axi_bvalid <= 0;
            if (write_fire) begin
                aw_pending <= 0; w_pending <= 0; s_axi_bvalid <= 1; s_axi_bresp <= 0;
                case (aw_address)
                    8'h04: begin
                        if (start_cmd && (busy || !valid_length)) begin
                            s_axi_bresp <= 2; config_error <= 1;
                        end
                    end
                    8'h0c: begin
                        if (busy) s_axi_bresp <= 2;
                        else requested_frames <= strobed(requested_frames,w_data,w_strobes);
                    end
                    8'hac: begin
                        if(w_strobes[1:0]==2'b11 && w_data<808) diagnostic_index<=w_data[9:0];
                        else s_axi_bresp<=2;
                    end
                    8'h58: begin
                        if(w_strobes[0] && w_data[7:0]<202) doa_read_index<=w_data[7:0];
                        else if(w_strobes[0]) s_axi_bresp<=2;
                    end
                    default: s_axi_bresp <= 2;
                endcase
            end
            if (s_axi_rvalid && s_axi_rready) s_axi_rvalid <= 0;
            if (s_axi_arvalid && s_axi_arready) begin
                s_axi_rvalid <= 1; s_axi_rresp <= 0;
                case (s_axi_araddr)
                    8'h00: s_axi_rdata <= 32'h4d494301; // MIC, ABI 1
                    8'h04: s_axi_rdata <= {31'b0,clock_enable};
                    8'h08: s_axi_rdata <= {25'b0,config_error,aborted,overflow,done,busy,warm_ready,clock_enable};
                    8'h0c: s_axi_rdata <= requested_frames;
                    8'h10: s_axi_rdata <= captured_frames;
                    8'h14: s_axi_rdata <= sent_frames;
                    8'h18: s_axi_rdata <= warm_frames;
                    8'h1c: s_axi_rdata <= 31250;
                    8'h20: s_axi_rdata <= 2000000;
                    8'h24: s_axi_rdata <= 32'h08201820; // ch8, slot32, valid24, storage32
                    8'h28: s_axi_rdata <= fifo_count;
                    8'h2c: s_axi_rdata <= WARMUP_FRAMES;
                    8'h30: s_axi_rdata <= 32'h76543210;
                    8'ha8: s_axi_rdata <= 32'd808;
                    8'hac: s_axi_rdata <= {22'b0,diagnostic_index};
                    8'hb0: s_axi_rdata <= diagnostic_data[31:0];
                    8'hb4: s_axi_rdata <= diagnostic_data[63:32];
                    8'h40: s_axi_rdata <= 32'h444f4105; // M8 FIR physical peak ABI4
                    8'h44: s_axi_rdata <= {29'b0,doa_overrun,doa_active,doa_ready};
                    8'h48: s_axi_rdata <= DOA_WINDOW_FRAMES;
                    8'h4c: s_axi_rdata <= doa_frames;
                    8'h50: s_axi_rdata <= DOA_SKIP_FRAMES;
                    8'h54: s_axi_rdata <= 32'h04100604; // channels4, lag16, pairs6, shift4
                    8'h58: s_axi_rdata <= {24'b0,doa_read_index};
                    8'h5c: s_axi_rdata <= doa_read_data[31:0];
                    8'h60: s_axi_rdata <= doa_read_data[63:32];
                    8'h64: s_axi_rdata <= doa_peak_lags[31:0];
                    8'h68: s_axi_rdata <= {16'b0,doa_peak_lags[47:32]};
                    8'h6c: s_axi_rdata <= {24'b0,doa_temporal_support,doa_temporal_mask,doa_temporal_stable};
                    8'h70: s_axi_rdata <= DOA_WINDOW_FRAMES/4;
                    8'h74: s_axi_rdata <= 32'h00010304; // tolerance1, support3, blocks4
                    8'h78: s_axi_rdata <= doa_subwindow_peaks[31:0];
                    8'h7c: s_axi_rdata <= {16'b0,doa_subwindow_peaks[47:32]};
                    8'h80: s_axi_rdata <= doa_subwindow_peaks[79:48];
                    8'h84: s_axi_rdata <= {16'b0,doa_subwindow_peaks[95:80]};
                    8'h88: s_axi_rdata <= doa_subwindow_peaks[127:96];
                    8'h8c: s_axi_rdata <= {16'b0,doa_subwindow_peaks[143:128]};
                    8'h90: s_axi_rdata <= doa_subwindow_peaks[175:144];
                    8'h94: s_axi_rdata <= {16'b0,doa_subwindow_peaks[191:176]};
                    8'h98: s_axi_rdata <= 32'h060a0704; // ceil limits pairs 0..3
                    8'h9c: s_axi_rdata <= 32'h00000309; // ceil limits pairs 4..5
                    8'ha0: s_axi_rdata <= 32'h50504831; // positive physical peaks, invalid=-128
                    8'ha4: s_axi_rdata <= 32'h08040210; // M8 / input shift4 / output shift2 / 16 taps
                    default: begin s_axi_rdata <= 0; s_axi_rresp <= 2; end
                endcase
            end

            if (push_frame) begin
                fifo_data[wr_ptr] <= frame_data;
                fifo_last[wr_ptr] <= (captured_frames + 1 == requested_frames);
                wr_ptr <= wr_ptr + 1'b1;
                captured_frames <= captured_frames + 1'b1;
                if (captured_frames + 1 == requested_frames) collecting <= 0;
            end
            if (want_frame && !push_frame) begin overflow <= 1; collecting <= 0; end
            if (beat_taken) begin
                channel_index <= channel_index + 1'b1;
                if (pop_frame) begin
                    rd_ptr <= rd_ptr + 1'b1;
                    sent_frames <= sent_frames + 1'b1;
                    if (m_axis_tlast) begin busy <= 0; done <= 1; end
                end
            end
            case ({push_frame,pop_frame})
                2'b10: fifo_count <= fifo_count + 1'b1;
                2'b01: fifo_count <= fifo_count - 1'b1;
                default: ;
            endcase

            // Explicit abort/clock-off discards an incomplete stream. Software
            // must reset DMA before freeing/reusing its target allocation.
            if (control_write) begin
                if (!start_cmd || (!busy && valid_length)) clock_enable <= w_data[0];
                if (abort_cmd) begin
                    busy <= 0; collecting <= 0; done <= 0; aborted <= 1;
                    wr_ptr <= 0; rd_ptr <= 0; fifo_count <= 0; channel_index <= 0;
                    if (!w_data[0]) warm_frames <= 0;
                end else if (start_cmd && !busy && valid_length) begin
                    busy <= 1; collecting <= 1; done <= 0;
                    overflow <= 0; aborted <= 0; config_error <= 0;
                    captured_frames <= 0; sent_frames <= 0;
                    wr_ptr <= 0; rd_ptr <= 0; fifo_count <= 0; channel_index <= 0;
                end
            end
        end
    end
endmodule
