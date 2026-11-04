`timescale 1ns/1ps
// Snapshot engine, all in the 100 MHz domain. One product per three cycles.
// ABI4: M8 FIR, measured positive peaks inside fixed physical pair domains.
// The selected frame order is MIC01,05,07,08. No serial-slot timing correction.
module doa_correlator #(
    parameter integer WINDOW_FRAMES=32768,
    parameter integer SKIP_FRAMES=31250
)(
    input wire clk, resetn, arm, abort_capture,
    input wire sample_valid,
    input wire [255:0] frame_data,
    input wire [7:0] read_index,
    output wire [63:0] read_data,
    input wire [9:0] diagnostic_index,
    output wire [63:0] diagnostic_data,
    output reg ready, active, overrun,
    output reg [31:0] accumulated_frames,
    output wire [47:0] peak_lags,
    output reg [191:0] subwindow_peaks,
    output reg [3:0] temporal_match_mask,
    output reg [2:0] temporal_support,
    output reg temporal_stable
);
    localparam SUBWINDOW_FRAMES=WINDOW_FRAMES/4;
    localparam IDLE=0, READ_OP=1, MULTIPLY=2, ACCUMULATE=3, PEAK_READ=4, PEAK_COMPARE=5, FINISH=6;
    reg [2:0] state;
    reg [31:0] skipped, history_samples;
    reg [5:0] wr_ptr, center_ptr;
    reg [7:0] term_addr;
    reg [2:0] pair_index;
    reg [5:0] lag_index;
    reg signed [19:0] previous[0:3][0:14];
    wire signed [19:0] input_word[0:3];
    wire signed [21:0] filtered[0:3];
    wire signed [21:0] quad_sum[0:3][0:3];
    reg signed [21:0] quad_stage[0:3][0:3];
    reg signed [22:0] half_stage[0:3][0:1];
    reg fir_valid_0, fir_valid_1;
    (* ram_style="distributed" *) reg signed [21:0] history[0:3][0:63];
    (* ram_style="distributed" *) reg signed [63:0] sums[0:201];
    (* ram_style="distributed" *) reg signed [63:0] sub_sums[0:201];
    (* ram_style="distributed" *) reg signed [63:0] diagnostic_sums[0:807];
    reg [31:0] sub_frames;
    reg [1:0] sub_index;
    wire [9:0] diagnostic_write_index=sub_index*10'd202+{2'b0,term_addr};
    reg scanning_full;
    reg signed [21:0] operand_a, operand_b;
    (* use_dsp="yes" *) reg signed [43:0] product;
    wire signed [63:0] extended_product={{20{product[43]}},product};
    reg [63:0] peak_value, candidate;
    reg [5:0] peak_index;
    reg signed [7:0] peaks[0:5];
    wire [5:0] lag_address=center_ptr+lag_index-6'd16;
    wire [1:0] energy_channel=term_addr-8'd198;
    wire [3:0] guard_mask;
    wire [2:0] guard_support;
    wire guard_stable;
    doa_temporal_guard guard(.full_peaks(peak_lags),.sub_peaks(subwindow_peaks),
        .match_mask(guard_mask),.support(guard_support),.stable(guard_stable));
    assign diagnostic_data=(ready && diagnostic_index<808) ? diagnostic_sums[diagnostic_index] : 64'd0;
    assign read_data=(ready && read_index<202) ? sums[read_index] : 64'd0;
    assign peak_lags={peaks[5],peaks[4],peaks[3],peaks[2],peaks[1],peaks[0]};
    assign input_word[0]=$signed(frame_data[0*32 +: 32]) >>> 4;
    assign input_word[1]=$signed(frame_data[4*32 +: 32]) >>> 4;
    assign input_word[2]=$signed(frame_data[6*32 +: 32]) >>> 4;
    assign input_word[3]=$signed(frame_data[7*32 +: 32]) >>> 4;
    // Input >> 4; FIR [1]*8 + [-1]*8; arithmetic output >> 2.
    // Balanced, explicitly sign-extended sums avoid intermediate truncation.
    // Two register boundaries: accept quads, form halves, commit history.
    // Commit is two 100MHz cycles after acceptance; sample indices are unchanged.
    genvar g,h;
    generate for(g=0;g<4;g=g+1) begin: fir
        wire signed [19:0] tap[0:15];
        wire signed [20:0] pair_sum[0:7];

        wire signed [23:0] difference;
        assign tap[0]=input_word[g];
        for(h=1;h<16;h=h+1) begin: taps
            assign tap[h]=previous[g][h-1];
        end
        for(h=0;h<8;h=h+1) begin: pairs
            assign pair_sum[h]=$signed({tap[2*h][19],tap[2*h]})
                +$signed({tap[2*h+1][19],tap[2*h+1]});
        end
        for(h=0;h<4;h=h+1) begin: quads
            assign quad_sum[g][h]=$signed({pair_sum[2*h][20],pair_sum[2*h]})
                +$signed({pair_sum[2*h+1][20],pair_sum[2*h+1]});
        end
        assign difference=$signed({half_stage[g][0][22],half_stage[g][0]})
            -$signed({half_stage[g][1][22],half_stage[g][1]});
        assign filtered[g]=difference >>> 2;
    end endgenerate
    function automatic [1:0] channel_i(input [2:0] p);
        case(p) 0,1,2:channel_i=0; 3,4:channel_i=1; default:channel_i=2; endcase
    endfunction
    function automatic [1:0] channel_j(input [2:0] p);
        case(p) 0:channel_j=1; 1,3:channel_j=2; default:channel_j=3; endcase
    endfunction
    function automatic [5:0] search_limit(input [2:0] p);
        case(p) 0:search_limit=4;1:search_limit=7;2:search_limit=10;
                3:search_limit=6;4:search_limit=9;default:search_limit=3;endcase
    endfunction
    function automatic [63:0] admissible(input signed [63:0] value,input [2:0] p,input [5:0] index);
        admissible=(value>0 && index>=16-search_limit(p) && index<=16+search_limit(p)) ? value : 64'd0;
    endfunction
    wire [5:0] selected_index=(candidate>peak_value ? lag_index : peak_index);
    wire [63:0] selected_value=(candidate>peak_value ? candidate : peak_value);
    wire signed [7:0] selected_lag=(selected_value==0) ? -8'sd128 : $signed({2'b0,selected_index})-8'sd16;
    integer c,d;
    always @(posedge clk) begin
        if(!resetn || arm) begin
            ready<=0;active<=resetn && arm;overrun<=0;accumulated_frames<=0;
            skipped<=0;history_samples<=0;wr_ptr<=0;center_ptr<=0;
            fir_valid_0<=0;fir_valid_1<=0;
            sub_frames<=0;sub_index<=0;scanning_full<=0;subwindow_peaks<=0;
            temporal_match_mask<=0;temporal_support<=0;temporal_stable<=0;
            state<=IDLE;term_addr<=0;pair_index<=0;lag_index<=0;
            operand_a<=0;operand_b<=0;product<=0;peak_value<=0;candidate<=0;peak_index<=0;
            for(c=0;c<4;c=c+1) begin
                for(d=0;d<15;d=d+1) previous[c][d]<=0;
                for(d=0;d<4;d=d+1) quad_stage[c][d]<=0;
                for(d=0;d<2;d=d+1) half_stage[c][d]<=0;
            end
            for(c=0;c<6;c=c+1) peaks[c]<=0;
            // RAM is intentionally not reset: first accumulation overwrites every address.
        end else if(abort_capture && !ready) begin
            active<=0;state<=IDLE;fir_valid_0<=0;fir_valid_1<=0;
        end else begin
            fir_valid_0<=0;
            fir_valid_1<=fir_valid_0 && active;
            if(fir_valid_0 && active) begin
                for(c=0;c<4;c=c+1) for(d=0;d<2;d=d+1)
                    half_stage[c][d]<=$signed({quad_stage[c][2*d][21],quad_stage[c][2*d]})
                        +$signed({quad_stage[c][2*d+1][21],quad_stage[c][2*d+1]});
            end
            if(sample_valid && active) begin
                if(state!=IDLE || fir_valid_0 || fir_valid_1) overrun<=1;
                if(skipped<SKIP_FRAMES) skipped<=skipped+1;
                else begin
                    fir_valid_0<=1;
                    for(c=0;c<4;c=c+1) begin
                        for(d=0;d<4;d=d+1) quad_stage[c][d]<=quad_sum[c][d];
                        for(d=14;d>0;d=d-1) previous[c][d]<=previous[c][d-1];
                        previous[c][0]<=input_word[c];
                    end
                end
            end
            if(fir_valid_1 && active) begin
                for(c=0;c<4;c=c+1) history[c][wr_ptr]<=filtered[c];
                wr_ptr<=wr_ptr+1'b1;
                history_samples<=history_samples+1;
                if(history_samples>=35 && state==IDLE) begin
                    center_ptr<=wr_ptr-6'd16;term_addr<=0;pair_index<=0;lag_index<=0;state<=READ_OP;
                end
            end
            case(state)
                READ_OP: begin
                    if(term_addr<198) begin
                        operand_a<=history[channel_i(pair_index)][center_ptr];
                        operand_b<=history[channel_j(pair_index)][lag_address];
                    end else begin
                        operand_a<=history[energy_channel][center_ptr];
                        operand_b<=history[energy_channel][center_ptr];
                    end
                    state<=MULTIPLY;
                end
                MULTIPLY: begin product<=operand_a*operand_b;state<=ACCUMULATE;end
                ACCUMULATE: begin
                    sums[term_addr]<=(accumulated_frames==0 ? 64'sd0 : sums[term_addr])+extended_product;
                    sub_sums[term_addr]<=(sub_frames==0 ? 64'sd0 : sub_sums[term_addr])+extended_product;
                    if(sub_frames==SUBWINDOW_FRAMES-1 && term_addr>=198)
                        diagnostic_sums[diagnostic_write_index]<=
                            (sub_frames==0 ? 64'sd0 : sub_sums[term_addr])+extended_product;
                    if(term_addr==201) begin
                        accumulated_frames<=accumulated_frames+1;
                        if(accumulated_frames==WINDOW_FRAMES-1) active<=0;
                        if(sub_frames==SUBWINDOW_FRAMES-1) begin
                            sub_frames<=0;scanning_full<=0;
                            state<=PEAK_READ;term_addr<=0;pair_index<=0;lag_index<=0;
                            peak_value<=0;peak_index<=0;
                        end else begin sub_frames<=sub_frames+1;state<=IDLE;end
                    end else begin
                        term_addr<=term_addr+1;
                        if(lag_index==32) begin lag_index<=0;pair_index<=pair_index+1;end
                        else lag_index<=lag_index+1;
                        state<=READ_OP;
                    end
                end
                PEAK_READ: begin
                    if(!scanning_full) diagnostic_sums[diagnostic_write_index]<=sub_sums[term_addr];
                    candidate<=admissible(scanning_full ? sums[term_addr] : sub_sums[term_addr],pair_index,lag_index);
                    state<=PEAK_COMPARE;
                end
                PEAK_COMPARE: begin
                    if(candidate>peak_value) begin peak_value<=candidate;peak_index<=lag_index;end
                    if(lag_index==32) begin
                        if(scanning_full)
                            peaks[pair_index]<=selected_lag;
                        else
                            subwindow_peaks[sub_index*48+pair_index*8+:8]<=
                                selected_lag;
                        if(pair_index==5) begin
                            if(scanning_full) state<=FINISH;
                            else if(accumulated_frames==WINDOW_FRAMES) begin
                                scanning_full<=1;state<=PEAK_READ;
                                pair_index<=0;lag_index<=0;term_addr<=0;peak_value<=0;peak_index<=0;
                            end else begin sub_index<=sub_index+1;state<=IDLE;end
                        end
                        else begin
                            pair_index<=pair_index+1;lag_index<=0;term_addr<=term_addr+1;
                            peak_value<=0;peak_index<=0;state<=PEAK_READ;
                        end
                    end else begin lag_index<=lag_index+1;term_addr<=term_addr+1;state<=PEAK_READ;end
                end
                FINISH: begin
                    temporal_match_mask<=guard_mask;temporal_support<=guard_support;
                    temporal_stable<=guard_stable && !overrun;
                    ready<=!overrun;state<=IDLE;
                end
                default: ;
            endcase
        end
    end
endmodule
