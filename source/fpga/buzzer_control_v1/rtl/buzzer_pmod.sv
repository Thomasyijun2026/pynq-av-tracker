module buzzer_pmod(input wire aclk,input wire aresetn,input wire [31:0] command,
 output wire [31:0] status,inout wire buzzer_io);
 wire drive,enabled,sampled;
 (* ASYNC_REG="TRUE", SHREG_EXTRACT="NO" *) reg pad_meta=0,pad_sync=0;
 always @(posedge aclk)begin
  if(!aresetn)begin pad_meta<=0;pad_sync<=0;end
  else begin pad_meta<=sampled;pad_sync<=pad_meta;end
 end
 buzzer_oneshot core(.aclk(aclk),.aresetn(aresetn),.command(command),
  .status(status),.pin_value(drive),.pin_enable(enabled),.pin_readback(pad_sync));
 IOBUF buzzer_pad(.I(drive),.T(!enabled),.O(sampled),.IO(buzzer_io));
endmodule
