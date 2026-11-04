// AXI GPIO command/status adapter, 100 MHz production clock.
// Reset/unarmed: high impedance. Armed idle: explicit inactive level.
// A trigger cannot extend an existing pulse. 100 ms recovery between pulses.
module buzzer_oneshot #(
 parameter integer CYCLES_PER_MS=100000,
 parameter integer MAX_MS=1000,
 parameter integer COOLDOWN_MS=100
)(input wire aclk,input wire aresetn,input wire [31:0] command,
 output wire [31:0] status,output wire pin_value,output wire pin_enable,
 input wire pin_readback);
 localparam integer CW=(CYCLES_PER_MS<2)?1:$clog2(CYCLES_PER_MS);
 localparam integer PW=(CYCLES_PER_MS/4<2)?1:$clog2(CYCLES_PER_MS/4);
 reg armed=0,busy=0,low_active=0,pwm=0,phase=0,token=0;
 reg fault=0,clipped=0;reg [7:0] completed=0;
 reg [15:0] remaining=0,cooldown=0;
 reg [CW-1:0] tick=0;reg [PW-1:0] pwm_tick=0;
 wire requested=command[28]!=token;
 assign pin_enable=armed;
 assign pin_value=(busy && (!pwm || phase)) ^ low_active;
 assign status={16'h425a,completed,fault,clipped,phase,pin_readback,
                (cooldown!=0),token,busy,armed};
 always @(posedge aclk) begin
  if(!aresetn) begin
   armed<=0;busy<=0;low_active<=0;pwm<=0;phase<=0;token<=0;
   fault<=0;clipped<=0;completed<=0;remaining<=0;cooldown<=0;tick<=0;pwm_tick<=0;
  end else if(!command[31]) begin
   armed<=0;busy<=0;phase<=0;remaining<=0;cooldown<=0;token<=command[28];tick<=0;
  end else if(!armed) begin
   // An enable edge never triggers a pulse, irrespective of the token value.
   armed<=1;busy<=0;low_active<=command[30];pwm<=command[29];token<=command[28];
   fault<=|command[27:16];clipped<=0;phase<=0;remaining<=0;cooldown<=0;tick<=0;
  end else if(command[27:16]!=0 || command[30]!=low_active || command[29]!=pwm) begin
   // Polarity/mode changes require disarm; never change an active pin in place.
   busy<=0;phase<=0;remaining<=0;fault<=1;token<=command[28];
   cooldown<=COOLDOWN_MS;tick<=CYCLES_PER_MS-1;
  end else if(command[15:0]==0) begin
   // STOP is independent of the request token, and leaves the inactive level driven.
   if(busy)begin cooldown<=COOLDOWN_MS;tick<=CYCLES_PER_MS-1;end
   else if(cooldown!=0)begin
    if(tick==0)begin tick<=CYCLES_PER_MS-1;cooldown<=cooldown-1'b1;end
    else tick<=tick-1'b1;
   end
   busy<=0;phase<=0;remaining<=0;token<=command[28];
  end else begin
   if(requested)begin
    token<=command[28];
    if(busy || cooldown!=0)fault<=1;
    else begin
     busy<=1;phase<=1;tick<=CYCLES_PER_MS-1;pwm_tick<=CYCLES_PER_MS/4-1;
     remaining<=(command[15:0]>MAX_MS)?MAX_MS:command[15:0];
     clipped<=command[15:0]>MAX_MS;fault<=0;
    end
   end
   if(busy)begin
    if(tick==0)begin
     tick<=CYCLES_PER_MS-1;
     if(remaining==1)begin
      busy<=0;phase<=0;remaining<=0;completed<=completed+1'b1;cooldown<=COOLDOWN_MS;
     end else remaining<=remaining-1'b1;
    end else tick<=tick-1'b1;
    if(pwm && !(tick==0 && remaining==1))begin
     if(pwm_tick==0)begin pwm_tick<=CYCLES_PER_MS/4-1;phase<=!phase;end
     else pwm_tick<=pwm_tick-1'b1;
    end
   end else if(cooldown!=0)begin
    if(tick==0)begin tick<=CYCLES_PER_MS-1;cooldown<=cooldown-1'b1;end
    else tick<=tick-1'b1;
   end
  end
 end
endmodule
