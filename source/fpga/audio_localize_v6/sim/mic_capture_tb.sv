`timescale 1ns/1ps
module mic_capture_tb;
  reg clk=0; always #5 clk=~clk;
  reg resetn=0;
  reg [7:0] awaddr=0,araddr=0;
  reg [31:0] wdata=0;
  reg [3:0] wstrb=0;
  reg awvalid=0,wvalid=0,bready=0,arvalid=0,rready=0;
  wire awready,wready,bvalid,arready,rvalid;
  wire [1:0] bresp,rresp;
  wire [31:0] rdata,tdata;
  wire [3:0] tkeep;
  wire tvalid,tlast,bclk,ws;
  reg ready_enable=0,random_ready=0;
  reg [31:0] lfsr=32'hc01df00d;
  wire tready=ready_enable && (!random_ready || lfsr[0] || lfsr[3]);
  reg [3:0] sd=0;
  mic_capture_axi #(.DOA_WINDOW_FRAMES(8),.DOA_SKIP_FRAMES(0)) dut(.aclk(clk),.aresetn(resetn),
    .s_axi_awaddr(awaddr),.s_axi_awprot(3'b0),.s_axi_awvalid(awvalid),.s_axi_awready(awready),
    .s_axi_wdata(wdata),.s_axi_wstrb(wstrb),.s_axi_wvalid(wvalid),.s_axi_wready(wready),
    .s_axi_bresp(bresp),.s_axi_bvalid(bvalid),.s_axi_bready(bready),
    .s_axi_araddr(araddr),.s_axi_arprot(3'b0),.s_axi_arvalid(arvalid),.s_axi_arready(arready),
    .s_axi_rdata(rdata),.s_axi_rresp(rresp),.s_axi_rvalid(rvalid),.s_axi_rready(rready),
    .m_axis_tdata(tdata),.m_axis_tkeep(tkeep),.m_axis_tlast(tlast),
    .m_axis_tvalid(tvalid),.m_axis_tready(tready),.mic_sd(sd),.mic_bclk(bclk),.mic_ws(ws));
  integer checks=0,beats=0,goal_frames=0,first_frame=-1,source_frame=0,slot_bit=31;
  integer lane,ch,f;
  reg prev_ws=1,scoreboard=0,held=0,allow_abort=0;
  reg [31:0] held_data;
  reg held_last;
  reg [23:0] source_word;
  reg [3:0] next_sd;
  time enabled_at=0,last_bclk=0,last_ws=0;
  reg timing_active=0;
  function automatic [31:0] pattern(input integer frame_no,input integer channel_no);
    reg [23:0] v;
    begin
      case(channel_no)
        0:v=frame_no; 1:v=-frame_no-33; 2:v=24'h7fffff; 3:v=24'h800000;
        4:v=24'h123456 ^ frame_no; 5:v=24'habcdef ^ frame_no;
        6:v=0; 7:v=(frame_no&1)?24'h000001:24'hffffff;
      endcase
      pattern={{8{v[23]}},v};
    end
  endfunction
  task automatic check(input bit condition,input string description);
    begin
      if (!condition) $fatal(1,"FAIL %s at %0t",description,$time);
      checks=checks+1;
    end
  endtask
  // Independent pin-level transmitter: one delay bit, 24 data bits, poison padding.
  // Delay varies from 0 to 170 ns after falling BCLK (420 ns from prior rising).
  always @(negedge bclk) begin
    #1;
    if(resetn && dut.clock_enable) begin
      if(ws != prev_ws) begin
        slot_bit=0;
        if(!ws) source_frame=source_frame+1;
      end else slot_bit=slot_bit+1;
      prev_ws=ws;
      next_sd=4'ha;
      if(slot_bit>=1 && slot_bit<=24) begin
        for(lane=0;lane<4;lane=lane+1) begin
          source_word=pattern(source_frame,2*lane+ws);
          next_sd[lane]=source_word[24-slot_bit];
        end
      end
      #( (source_frame % 3)*85 );
      sd=next_sd;
    end else begin prev_ws=1;slot_bit=31;sd=0; end
  end
  always @(bclk) if(timing_active && resetn && dut.clock_enable) begin
    if(last_bclk!=0) check($time-last_bclk==250,"BCLK half period 250 ns");
    last_bclk=$time;
  end
  always @(ws) if(timing_active && resetn && dut.clock_enable) begin
    if(last_ws!=0) check($time-last_ws==16000,"WS half period 32 BCLK");
    last_ws=$time;
  end
  always @(posedge clk) begin
    if(tvalid) lfsr<={lfsr[30:0],lfsr[31]^lfsr[21]^lfsr[1]^lfsr[0]};
    if(resetn) begin
      if(held && !allow_abort) check(tvalid && tdata===held_data && tlast===held_last,"stable stalled AXIS");
      held=tvalid && !tready;
      held_data=tdata;held_last=tlast;
      if(tvalid) check($time-enabled_at>=131072000,"full 2^18 BCLK startup elapsed");
      if(tvalid && tready && scoreboard) begin
        ch=beats%8;
        if(beats==0) first_frame=tdata;
        f=first_frame+beats/8;
        check(tdata===pattern(f,ch),$sformatf("sample frame=%0d channel=%0d expected=%h got=%h",f,ch,pattern(f,ch),tdata));
        check(tkeep==15,"all four byte lanes valid");
        check(tlast==((beats+1)==goal_frames*8),"TLAST exact final sample");
        beats=beats+1;
        check(beats<=goal_frames*8,"no excess or repeated frame");
      end
    end else held=0;
  end
  task automatic put_aw(input [7:0] address,input integer delay_cycles);
    begin repeat(delay_cycles) @(negedge clk); awaddr=address;awvalid=1;
      do @(posedge clk); while(!awready);
      @(negedge clk);awvalid=0;
    end
  endtask
  task automatic put_w(input [31:0] data,input [3:0] st,input integer delay_cycles);
    begin repeat(delay_cycles) @(negedge clk);wdata=data;wstrb=st;wvalid=1;
      do @(posedge clk);while(!wready);
      @(negedge clk);wvalid=0;
    end
  endtask
  task automatic wr(input [7:0] address,input [31:0] data,input [3:0] st,input integer order,input [1:0] response);
    reg [1:0] saved;
    begin
      @(negedge clk);
      fork put_aw(address,order==1?4:0);put_w(data,st,order==2?4:0);join
      while(!bvalid) @(negedge clk);
      saved=bresp;check(bresp==response,"AXIL BRESP");
      repeat(3) begin @(negedge clk);check(bvalid && bresp==saved,"held write response");end
      bready=1;@(negedge clk);bready=0;
    end
  endtask
  task automatic rd(input [7:0] address,output [31:0] data,input [1:0] response);
    reg [31:0] saved;
    begin
      @(negedge clk);araddr=address;arvalid=1;
      do @(posedge clk);while(!arready);
      @(negedge clk);arvalid=0;
      while(!rvalid) @(negedge clk);
      data=rdata;saved=rdata;check(rresp==response,"AXIL RRESP");
      repeat(3) begin @(negedge clk);check(rvalid && rdata==saved && rresp==response,"held read response");end
      rready=1;@(negedge clk);rready=0;
    end
  endtask
  task automatic arm(input integer n);
    begin
      beats=0;first_frame=-1;goal_frames=n;scoreboard=1;
      wr(8'h0c,n,15,1,0);wr(8'h04,3,1,2,0);
    end
  endtask
  task automatic wait_done(input integer n);
    reg [31:0] status,v;integer tries;
    begin
      status=0;tries=0;
      while(!status[3] && tries<6000) begin #32000;rd(8'h08,status,0);tries=tries+1;end
      check(status[3] && !status[2] && !status[4],"finite capture completed");
      check(beats==n*8,"exact stream byte count");
      rd(8'h10,v,0);check(v==n,"captured frame count");
      rd(8'h14,v,0);check(v==n,"sent frame count");
      #64000;check(beats==n*8,"stream stops at requested length");scoreboard=0;
    end
  endtask
  reg [31:0] v;
  // Independent scalar 64-bit source oracle, including zero initial history.
  function automatic longint signed filtered_word(input integer f,input integer ch);
    longint signed value,total;
    integer tap;
    begin
      total=0;
      for(tap=0;tap<16;tap=tap+1) begin
        value=(f-tap<first_frame) ? 0 : $signed(pattern(f-tap,ch));
        if(tap<8) total=total+(value>>>4);
        else total=total-(value>>>4);
      end
      filtered_word=total>>>2;
    end
  endfunction
  task automatic verify_doa;
    integer i,j,k,t,index,ci,cj,q,p,max_k,support_count,delta,limit;
    longint signed gold,fa,fb;
    reg [63:0] mag,max_mag;
    reg [47:0] expected_full,expected_sub[0:3],packed_peaks;
    reg [3:0] mask;
    reg [31:0] lo,hi;
    begin
      rd(8'h40,lo,0);check(lo==32'h444f4105,"DOA extension ABI5");
      $display("FIR_ORACLE_DEBUG anchor=%0d p0=%h p4=%h y0_19=%0d y4_3=%0d y0_26=%0d",first_frame,pattern(first_frame+19,0),pattern(first_frame+3,4),filtered_word(first_frame+19,0),filtered_word(first_frame+3,4),filtered_word(first_frame+26,0));
      rd(8'h44,lo,0);check(lo==1,"DOA ready no overrun");
      rd(8'h4c,lo,0);check(lo==8,"DOA exact window length");
      index=0;
      for(i=0;i<4;i=i+1) for(j=i+1;j<4;j=j+1) begin
        ci=(i==0)?0:((i==1)?4:((i==2)?6:7));
        cj=(j==0)?0:((j==1)?4:((j==2)?6:7));
        for(k=-16;k<=16;k=k+1) begin
          gold=0;
          for(t=19;t<27;t=t+1) begin
            fa=filtered_word(first_frame+t,ci);fb=filtered_word(first_frame+t+k,cj);
            if(index==0 && t==19) $display("FIR_PRODUCT_DEBUG ci=%0d cj=%0d t=%0d fa=%0d fb=%0d product=%0d",ci,cj,t,fa,fb,fa*fb);
            gold=gold+fa*fb;
          end
          wr(8'h58,index,1,0,0);rd(8'h5c,lo,0);rd(8'h60,hi,0);
          if({hi,lo}!==gold) $display("FIR_GOLD_MISMATCH index=%0d lag=%0d first_frame=%0d actual=%h gold=%h",index,k,first_frame,{hi,lo},gold);
          check({hi,lo}===gold,"AXIL DOA feature matches I2S source");index=index+1;
        end
      end
      for(i=0;i<4;i=i+1) begin
        ci=(i==0)?0:((i==1)?4:((i==2)?6:7));gold=0;
        for(t=19;t<27;t=t+1) begin fa=filtered_word(first_frame+t,ci);gold=gold+fa*fa;end
        wr(8'h58,index,1,0,0);rd(8'h5c,lo,0);rd(8'h60,hi,0);
        check({hi,lo}===gold,"AXIL DOA energy matches I2S source");index=index+1;
      end
      wr(8'h58,255,1,0,2);rd(8'h58,lo,0);check(lo==201,"invalid feature index rejected");
      // Independent source-level correlation oracle for all five peak groups.
      for(q=0;q<5;q=q+1) begin
        p=0;packed_peaks=0;
        for(i=0;i<4;i=i+1) for(j=i+1;j<4;j=j+1) begin
          ci=(i==0)?0:((i==1)?4:((i==2)?6:7));
          cj=(j==0)?0:((j==1)?4:((j==2)?6:7));
          max_mag=0;max_k=-128;
          case(p) 0:limit=4;1:limit=7;2:limit=10;3:limit=6;4:limit=9;default:limit=3;endcase
          for(k=-16;k<=16;k=k+1) begin
            gold=0;
            for(t=(q==4 ? 19 : 19+q*2);t<(q==4 ? 27 : 21+q*2);t=t+1) begin
              fa=filtered_word(first_frame+t,ci);fb=filtered_word(first_frame+t+k,cj);
              gold=gold+fa*fb;
            end
            mag=(gold>0 && k>=-limit && k<=limit) ? gold : 0;
            if(mag>max_mag) begin max_mag=mag;max_k=k;end
          end
          packed_peaks[p*8+:8]=max_k;p=p+1;
        end
        if(q==4) begin
          expected_full=packed_peaks;rd(8'h64,lo,0);rd(8'h68,hi,0);
        end else begin
          expected_sub[q]=packed_peaks;rd(8'h78+q*8,lo,0);rd(8'h7c+q*8,hi,0);
        end
        check({hi[15:0],lo}===packed_peaks && hi[31:16]==0,"AXIL full/subwindow peaks");
      end
      mask=4'hf;
      for(q=0;q<4;q=q+1) for(p=0;p<6;p=p+1) begin
        delta=$signed(expected_full[p*8+:8])-$signed(expected_sub[q][p*8+:8]);
        if(delta>1 || delta< -1 || expected_full[p*8+:8]==8'h80 || expected_sub[q][p*8+:8]==8'h80) mask[q]=0;
      end
      support_count=0;for(q=0;q<4;q=q+1) if(mask[q]) support_count=support_count+1;
      rd(8'h6c,lo,0);check(lo==((support_count<<5)|(mask<<1)|(support_count>=3)),"AXIL temporal guard");
      wr(8'h6c,0,15,0,2);rd(8'h6c,hi,0);check(hi==lo,"guard read-only");
      rd(8'h70,lo,0);check(lo==2,"subwindow length");
      rd(8'h74,lo,0);check(lo==32'h00010304,"fixed temporal guard config");
      rd(8'h98,lo,0);check(lo==32'h060a0704,"physical domains pairs 0..3");
      rd(8'h9c,lo,0);check(lo==32'h00000309,"physical domains pairs 4..5");
      rd(8'ha0,lo,0);check(lo==32'h50504831,"positive physical peak ABI");
      wr(8'ha0,0,15,0,2);rd(8'ha0,lo,0);check(lo==32'h50504831,"peak policy read-only");
      rd(8'ha4,lo,0);check(lo==32'h08040210,"M8 FIR identity");
      rd(8'ha8,lo,0);check(lo==808,"diagnostic shape");
      for(q=0;q<4;q=q+1) begin
        index=q*202;
        for(i=0;i<4;i=i+1) for(j=i+1;j<4;j=j+1) begin
          ci=((i==0)?0:((i==1)?4:((i==2)?6:7)));cj=((j==0)?0:((j==1)?4:((j==2)?6:7)));
          for(k=-16;k<=16;k=k+1) begin
            gold=0;
            for(t=19+q*2;t<21+q*2;t=t+1) begin
              fa=filtered_word(first_frame+t,ci);fb=filtered_word(first_frame+t+k,cj);gold=gold+fa*fb;
            end
            wr(8'hac,index,15,0,0);rd(8'hb0,lo,0);rd(8'hb4,hi,0);
            check({hi,lo}===gold,"AXIL subwindow correlation independent serial oracle");index=index+1;
          end
        end
        for(i=0;i<4;i=i+1) begin
          ci=((i==0)?0:((i==1)?4:((i==2)?6:7)));gold=0;
          for(t=19+q*2;t<21+q*2;t=t+1) begin fa=filtered_word(first_frame+t,ci);gold=gold+fa*fa;end
          wr(8'hac,index,15,0,0);rd(8'hb0,lo,0);rd(8'hb4,hi,0);
          check({hi,lo}===gold,"AXIL subwindow energy independent serial oracle");index=index+1;
        end
      end
      wr(8'hac,808,15,0,2);rd(8'hac,lo,0);check(lo==807,"invalid diagnostic index rejected");
      wr(8'hac,1,1,0,2);rd(8'hac,lo,0);check(lo==807,"partial diagnostic index rejected");
      wr(8'ha8,0,15,0,2);rd(8'ha8,lo,0);check(lo==808,"shape read only");
      wr(8'ha4,0,15,0,2);rd(8'ha4,lo,0);check(lo==32'h08040210,"FIR identity read-only");
    end
  endtask
  initial begin
    $display("MIC_CAPTURE_TEST_START");
    #100;@(negedge clk);resetn=1;
    rd(0,v,0);check(v==32'h4d494301,"ABI identity");
    rd(8'h1c,v,0);check(v==31250,"actual sample rate");
    rd(8'h20,v,0);check(v==2000000,"actual BCLK");
    rd(8'h2c,v,0);check(v==4096,"production warmup");
    rd(8'hfc,v,2);wr(8'hfc,1,15,0,2);
    wr(8'h0c,0,15,1,0);wr(4,3,1,2,2);rd(8,v,0);check(v[6]&&!v[0],"invalid zero length rejected");
    wr(8'h0c,32'h200000,15,0,0);wr(4,3,1,0,2);
    wr(8'h0c,32'h12345678,15,0,0);wr(8'h0c,20,1,2,0);
    rd(8'h0c,v,0);check(v==32'h12345614,"byte strobes");
    wr(4,3,0,0,0);rd(4,v,0);check(v==0,"zero strobes no side effect");
    enabled_at=$time;timing_active=1;ready_enable=0;
    arm(20);
    wr(8'h0c,9,15,0,2); // busy configuration cannot change the capture length
    #131000000;check(!tvalid && beats==0,"no startup samples");
    #180000; // collect several whole frames under sustained backpressure
    random_ready=1;ready_enable=1;wait_done(20);
    $display("PASS startup, signed limits, I2S phase, 8-channel order, zero input, AXIL and randomized backpressure");
    arm(1);wait_done(1);
    arm(37);wait_done(37);
    arm(64);wait_done(64);verify_doa();
    $display("PASS one-frame and repeated finite capture without clock restart");
    ready_enable=0;scoreboard=0;
    wr(8'h0c,40,15,0,0);wr(4,3,1,0,0);
    #700000;rd(8,v,0);check(v[4]&&v[2]&&!v[3],"overflow visible and capture incomplete");
    rd(8'h10,v,0);check(v==16,"overflow does not silently overwrite FIFO");
    allow_abort=1;wr(4,5,1,0,0);#100;held=0;allow_abort=0;
    rd(8,v,0);check(!v[2]&&v[5],"abort flag and idle");check(!tvalid,"abort flushes FIFO");
    ready_enable=1;arm(3);wait_done(3);
    $display("PASS overflow, abort and rearm");
    timing_active=0;wr(4,0,1,0,0);#1000;
    check(!bclk,"clock disabled");rd(8'h18,v,0);check(v==0,"clock-off clears warmup");
    enabled_at=$time;last_bclk=0;last_ws=0;timing_active=1;arm(2);
    #1000000;check(!tvalid,"clock restart requires new warmup");
    timing_active=0;allow_abort=1;@(negedge clk);resetn=0;#100;
    check(!bclk&&!tvalid,"reset cancels transaction and clock");@(negedge clk);resetn=1;
    rd(8,v,0);check(v==0,"reset status clean");
    $display("MIC_CAPTURE_TEST_PASS checks=%0d simulation_ns=%0t",checks,$time);$finish;
  end
  initial begin #500000000;$fatal(1,"simulation watchdog timeout");end
  initial begin
    #10000;$display("MIC_CAPTURE_TEST_PROGRESS ns=%0t",$time);
    repeat(15) begin #10000000;$display("MIC_CAPTURE_TEST_PROGRESS ns=%0t",$time);end
  end
endmodule
