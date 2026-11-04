`timescale 1ns/1ps
// One 100 MHz domain. BCLK is an output, never an internal fabric clock.
module i2s_rx (
    input wire clk, resetn, enable,
    input wire [3:0] mic_sd,
    output reg mic_bclk, mic_ws,
    output reg frame_valid,
    output reg [255:0] frame_data
);
    reg [4:0] half_count;
    reg [5:0] bit_index;
    // Preserve these four named CE capture registers for scoped I/O constraints.
    (* KEEP = "TRUE" *) reg [3:0] sd_sample;
    reg sample_valid;
    reg [5:0] sample_index;
    reg [23:0] shift_word [0:3];
    reg [23:0] left_word [0:3];
    integer i;
    always @(posedge clk) begin
        if (!resetn || !enable) begin
            half_count <= 0; bit_index <= 63;
            mic_bclk <= 0; mic_ws <= 1;
            frame_valid <= 0; frame_data <= 0;
            sample_valid <= 0; sample_index <= 0; sd_sample <= 0;
            for (i=0; i<4; i=i+1) begin
                shift_word[i] <= 0; left_word[i] <= 0;
            end
        end else begin
            frame_valid <= 0;
            sample_valid <= 0;
            if (half_count == 24) begin
                half_count <= 0;
                mic_bclk <= !mic_bclk;
                if (!mic_bclk) begin
                    // Latch at the FCLK edge that launches the BCLK rising edge.
                    sd_sample <= mic_sd;
                    sample_valid <= 1;
                    sample_index <= bit_index;
                end else begin
                    bit_index <= bit_index + 1'b1;
                    if (bit_index == 63) mic_ws <= 0;
                    if (bit_index == 31) mic_ws <= 1;
                end
            end else half_count <= half_count + 1'b1;
            if (sample_valid) begin
                // Indices 0 and 32 are the I2S delay bit. Padding is ignored.
                if ((sample_index[4:0] >= 1) && (sample_index[4:0] <= 24)) begin
                    for (i=0; i<4; i=i+1) begin
                        shift_word[i] <= {shift_word[i][22:0], sd_sample[i]};
                        if (sample_index == 24)
                            left_word[i] <= {shift_word[i][22:0], sd_sample[i]};
                        if (sample_index == 56) begin
                            frame_data[(2*i)*32 +: 32] <= {{8{left_word[i][23]}},left_word[i]};
                            frame_data[(2*i+1)*32 +: 32] <= {{8{shift_word[i][22]}},shift_word[i][22:0],sd_sample[i]};
                        end
                    end
                    if (sample_index == 56) frame_valid <= 1;
                end
            end
        end
    end
endmodule
