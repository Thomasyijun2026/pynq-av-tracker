`timescale 1ns/1ps
module doa_correlator_tb;
    reg clk=0;always #5 clk=~clk;
    reg resetn=0,arm=0,abort_capture=0,sample_valid=0,long_mode=0;
    reg [255:0] frame_data=0;
    reg [9:0] diagnostic_index=0;
    wire [63:0] ds_short,ds_long;
    wire [63:0] diagnostic_data=long_mode?ds_long:ds_short;
    reg [63:0] expected_diagnostic[0:807];
    reg [7:0] read_index=0;
    wire [63:0] rd_short,rd_long;
    wire r_short,r_long,a_short,a_long,o_short,o_long;
    wire [31:0] c_short,c_long;
    wire [47:0] p_short,p_long;
    wire [191:0] q_short,q_long;
    wire [3:0] m_short,m_long;
    wire [2:0] s_short,s_long;
    wire t_short,t_long;
    wire ready=long_mode?r_long:r_short;
    wire active=long_mode?a_long:a_short;
    wire overrun=long_mode?o_long:o_short;
    wire [31:0] count=long_mode?c_long:c_short;
    wire [63:0] read_data=long_mode?rd_long:rd_short;
    wire [47:0] peaks=long_mode?p_long:p_short;
    wire [191:0] sub_peaks=long_mode?q_long:q_short;
    wire [7:0] guard=long_mode?{s_long,m_long,t_long}:{s_short,m_short,t_short};
    doa_correlator #(.WINDOW_FRAMES(256),.SKIP_FRAMES(8)) small_dut(
        .clk(clk),.resetn(resetn),.arm(arm&&!long_mode),.abort_capture(abort_capture&&!long_mode),
        .sample_valid(sample_valid&&!long_mode),.frame_data(frame_data),.read_index(read_index),.read_data(rd_short),.diagnostic_index(diagnostic_index),.diagnostic_data(ds_short),
        .ready(r_short),.active(a_short),.overrun(o_short),.accumulated_frames(c_short),.peak_lags(p_short),
        .subwindow_peaks(q_short),.temporal_match_mask(m_short),.temporal_support(s_short),.temporal_stable(t_short));
    doa_correlator #(.WINDOW_FRAMES(32768),.SKIP_FRAMES(0)) full_dut(
        .clk(clk),.resetn(resetn),.arm(arm&&long_mode),.abort_capture(abort_capture&&long_mode),
        .sample_valid(sample_valid&&long_mode),.frame_data(frame_data),.read_index(read_index),.read_data(rd_long),.diagnostic_index(diagnostic_index),.diagnostic_data(ds_long),
        .ready(r_long),.active(a_long),.overrun(o_long),.accumulated_frames(c_long),.peak_lags(p_long),
        .subwindow_peaks(q_long),.temporal_match_mask(m_long),.temporal_support(s_long),.temporal_stable(t_long));
    reg [47:0] guard_full=0;
    reg [191:0] guard_sub=0;
    wire [3:0] guard_mask;
    wire [2:0] guard_count;
    wire guard_valid;
    doa_temporal_guard guard_dut(.full_peaks(guard_full),.sub_peaks(guard_sub),
        .match_mask(guard_mask),.support(guard_count),.stable(guard_valid));
    reg [255:0] input_frames[0:32802];
    reg [63:0] expected[0:201],expected_sub[0:791];
    reg [47:0] expected_peaks[0:4];
    reg [7:0] expected_guard[0:0];
    integer checks=0,case_id,n,k,p,q,frames,window_len,skip_len,mask,mask_count,gap,expected_delta;
    task automatic check(input bit ok,input string message);
        begin checks=checks+1;if(!ok) begin $display("FAIL %s case=%0d n=%0d k=%0d",message,case_id,n,k);$fatal;end end
    endtask
    task automatic start;
        begin @(negedge clk);arm=1;@(negedge clk);arm=0;
          check(active && !ready && !overrun && count==0 && guard==0,"arm clears snapshot and guard");end
    endtask
    task automatic sample(input [255:0] value,input integer cycles);
        begin @(negedge clk);frame_data=value;sample_valid=1;@(negedge clk);sample_valid=0;
          repeat(cycles-1) @(negedge clk);end
    endtask
    initial begin
        repeat(5) @(negedge clk);resetn=1;
        // Direct all-mask and exact +/-1 versus +/-2 comparisons, signed extremes.
        for(mask=0;mask<16;mask=mask+1) begin
            guard_full=0;guard_sub=0;mask_count=0;
            for(q=0;q<4;q=q+1) begin
                guard_sub[q*48+:8]=(mask&(1<<q)) ? -1 : 2;
                if(mask&(1<<q)) mask_count=mask_count+1;
            end
            #1;check(guard_mask==mask && guard_count==mask_count && guard_valid==(mask_count>=3),"all guard masks");
        end
        for(p=0;p<6;p=p+1) begin
            guard_full={6{8'h10}};guard_sub={24{8'h10}};
            guard_sub[p*8+:8]=8'hf0;#1;
            check(guard_mask==14 && guard_count==3 && guard_valid,"signed +16 to -16 difference");
            guard_full={6{8'hf0}};guard_sub={24{8'hf0}};
            guard_sub[p*8+:8]=8'h10;#1;
            check(guard_mask==14 && guard_count==3 && guard_valid,"signed -16 to +16 difference");
        end
        guard_full={6{8'h80}};guard_sub={24{8'h80}};#1;
        check(guard_mask==0 && guard_count==0 && !guard_valid,"all invalid peaks cannot be stable");
        // Pipeline lifetime: no early write, one commit, both pending ages cancelled.
        long_mode=1;start();
        @(negedge clk);frame_data=0;frame_data[0+:32]=32'sd8388607;
        frame_data[128+:32]=-32'sd8388608;sample_valid=1;
        @(negedge clk);sample_valid=0;
        check(full_dut.history_samples==0,"no write at FIR acceptance");
        @(negedge clk);check(full_dut.history_samples==0,"no write at FIR half stage");
        @(negedge clk);
        check(full_dut.history_samples==1 && full_dut.history[0][0]===22'sd131071
              && full_dut.history[1][0]===-22'sd131072,"two-cycle commit and signed extremes");
        repeat(6) @(negedge clk);check(full_dut.history_samples==1,"pause cannot duplicate frame");
        for(q=0;q<2;q=q+1) begin
            start();@(negedge clk);sample_valid=1;
            @(negedge clk);sample_valid=0;repeat(q) @(negedge clk);
            abort_capture=1;@(negedge clk);abort_capture=0;
            repeat(5) @(negedge clk);
            check(!active && !ready && full_dut.history_samples==0,"abort discards each pending age");
        end
        start();@(negedge clk);sample_valid=1;
        @(negedge clk);sample_valid=0;@(negedge clk);arm=1;
        @(negedge clk);arm=0;repeat(5) @(negedge clk);
        check(active && !ready && full_dut.history_samples==0,"rearm discards pending output");
        @(negedge clk);sample_valid=1;@(negedge clk);sample_valid=0;
        resetn=0;@(negedge clk);resetn=1;repeat(5) @(negedge clk);
        check(!active && full_dut.history_samples==0,"reset discards pending output");
        long_mode=0;start();
        for(n=0;n<8;n=n+1) sample(256'hffffffffffffffff,4);
        check(small_dut.history_samples==0 && !overrun,"skipped frames cannot enter pipeline");
        long_mode=1;start();@(negedge clk);sample_valid=1;
        repeat(2) @(negedge clk);sample_valid=0;repeat(5) @(negedge clk);
        check(overrun && !ready,"pipeline input collision is explicit overrun");
        for(case_id=0;case_id<8;case_id=case_id+1) begin
            long_mode=(case_id>=5);window_len=long_mode?32768:256;skip_len=long_mode?0:8;
            frames=window_len+35+skip_len;
            $readmemh($sformatf("case%0d_input.hex",case_id),input_frames,0,frames-1);
            $readmemh($sformatf("case%0d_expected.hex",case_id),expected);
            $readmemh($sformatf("case%0d_sub_sums.hex",case_id),expected_sub);
            $readmemh($sformatf("case%0d_diagnostic.hex",case_id),expected_diagnostic);
            $readmemh($sformatf("case%0d_peaks.hex",case_id),expected_peaks);
            $readmemh($sformatf("case%0d_guard.hex",case_id),expected_guard);
            start();
            for(n=0;n<frames;n=n+1) begin
                gap=650;
                if(n>=skip_len+35 && (n-skip_len-35+1)%(window_len/4)==0) gap=1500;
                sample(input_frames[n],gap);
                if(n>=skip_len+35 && (n-skip_len-35+1)%(window_len/4)==0) begin
                    q=(n-skip_len-35+1)/(window_len/4)-1;
                    for(k=0;k<198;k=k+1) begin
                        if(long_mode) check(full_dut.sub_sums[k]===expected_sub[q*198+k],"full-size subwindow int64");
                        else check(small_dut.sub_sums[k]===expected_sub[q*198+k],"short subwindow int64");
                    end
                    check(sub_peaks[q*48+:48]===expected_peaks[q+1],"subwindow peak ready before next sample");
                end
            end
            check(ready && !active && !overrun && count==window_len,"snapshot complete without overrun");
            for(k=0;k<202;k=k+1) begin
                read_index=k;#1;
                if(read_data!==expected[k]) $display("actual=%h expected=%h",read_data,expected[k]);
                check(read_data===expected[k],"all 202 full sums bit exact");
            end
            for(k=0;k<808;k=k+1) begin
                diagnostic_index=k;#1;
                check(diagnostic_data===expected_diagnostic[k],"808 subwindow sums and energies bit exact");
            end
            diagnostic_index=808;#1;check(diagnostic_data==0,"invalid diagnostic address hidden");
            diagnostic_index=807;
            check(peaks===expected_peaks[0],"all full peaks");
            check(guard===expected_guard[0],"guard mask support stable bit");
            for(q=0;q<4;q=q+1)check(sub_peaks[q*48+:48]===expected_peaks[q+1],"all subwindow peaks held");
            read_index=201;sample(256'hffffffff,1500);
            check(read_data===expected[201] && guard===expected_guard[0],"immutable after extra input");
            @(negedge clk);abort_capture=1;@(negedge clk);abort_capture=0;
            check(ready && read_data===expected[201] && guard===expected_guard[0],"snapshot survives cleanup");
            #1;check(diagnostic_data===expected_diagnostic[807],"diagnostics survive cleanup");
            $display("V4_FIR_CASE_PASS case=%0d frames=%0d guard=%h",case_id,frames,guard);
        end
        long_mode=0;start();sample(0,650);
        @(negedge clk);abort_capture=1;@(negedge clk);abort_capture=0;
        check(!ready && !active,"early abort");
        start();for(n=0;n<45;n=n+1) sample(0,1100);
        sample(0,2);sample(0,2);repeat(1500) @(negedge clk);
        check(overrun && !ready,"overrun reported");
        start();check(!overrun && guard==0,"rearm clears previous fault");
        diagnostic_index=0;#1;check(diagnostic_data==0,"rearm hides old diagnostics");
        read_index=255;#1;check(read_data==0,"uninitialized data not exposed");
        @(negedge clk);resetn=0;@(negedge clk);
        check(!ready && !active && guard==0,"reset clears state");
        $display("DOA_CORRELATOR_TEST_PASS checks=%0d",checks);$finish;
    end
    initial begin #1600000000;$display("FAIL simulation timeout");$fatal;end
endmodule
