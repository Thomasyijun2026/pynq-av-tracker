// Asynchronous physical inputs only. Debounce/edge semantics are in PS.
module button_sync(input wire aclk,input wire aresetn,input wire [3:0] btn_async,output wire [3:0] btn_sync);
 (* ASYNC_REG="TRUE", SHREG_EXTRACT="NO" *) reg [3:0] btn_meta;
 // Keep both stages explicit so the only false path terminates at btn_meta.
 // Vivado honors this attribute on the output register too.
 (* ASYNC_REG="TRUE", SHREG_EXTRACT="NO" *) reg [3:0] sync_stage;
 always @(posedge aclk) begin
  if (!aresetn) begin btn_meta<=0;sync_stage<=0;end
  else begin btn_meta<=btn_async;sync_stage<=btn_meta;end
 end
 assign btn_sync=sync_stage;
endmodule
