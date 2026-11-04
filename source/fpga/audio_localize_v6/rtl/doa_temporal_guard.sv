`timescale 1ns/1ps
// Four subwindows; every one of the six integer peaks must be within one
// sample of its full-window peak. No stored angle or previous result.
module doa_temporal_guard(
    input wire [47:0] full_peaks,
    input wire [191:0] sub_peaks,
    output wire [3:0] match_mask,
    output wire [2:0] support,
    output wire stable
);
    genvar q,p;
    generate for(q=0;q<4;q=q+1) begin: subwindow
        wire [5:0] agrees;
        for(p=0;p<6;p=p+1) begin: pair_check
            wire signed [7:0] a=full_peaks[p*8+:8];
            wire signed [7:0] b=sub_peaks[q*48+p*8+:8];
            wire signed [8:0] delta=$signed({a[7],a})-$signed({b[7],b});
            assign agrees[p]=(a!=8'sh80 && b!=8'sh80) && (delta >= -9'sd1 && delta <= 9'sd1);
        end
        assign match_mask[q]=&agrees;
    end endgenerate
    assign support={2'b0,match_mask[0]}+{2'b0,match_mask[1]}+
                   {2'b0,match_mask[2]}+{2'b0,match_mask[3]};
    assign stable=(support>=3);
endmodule
