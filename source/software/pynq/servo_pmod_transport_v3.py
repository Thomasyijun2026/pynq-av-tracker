"""Board-owned, bounded one-turn pan session. V1/V2 remain unchanged."""
from servo_pmod_transport_V1 import PmodReadOnlyTransport
from servo_pmod_transport_V2 import PmodTrialTransport
from servo_board_V3 import signed, word


class PmodPanTransport(PmodTrialTransport):
    def configure_range(self, configuration, sram, home, speed, acceleration):
        cfg, ram = bytes(configuration), bytes(sram)
        if self.armed or self.trial is not None or len(cfg)!=40 or len(ram)!=10 or ram[0]!=0:
            raise ValueError('Fresh torque-off configuration required')
        if type(home) is not int or not 0<=home<4096:
            raise ValueError('Initial single-turn position required')
        if speed not in (600,1200,1800) or acceleration not in (5,10,20):
            raise ValueError('Profile not in bounded candidate set')
        self.trial=dict(cfg=cfg,ram=ram,home=home,low=home-2048,high=home+2048,
                        speed=speed,acceleration=acceleration,rebased=False)

    def rebase(self, full_position):
        t=self.trial
        if (not self.armed or not self.torque_off or t is None or t['rebased'] or
            type(full_position) is not int or abs(full_position)>28000 or
            abs((full_position-t['home']+2048)%4096-2048)>2):
            raise ValueError('Full feedback rebase requires the stationary original pose')
        t.update(home=full_position,low=full_position-2048,high=full_position+2048,rebased=True)

    def validate(self, request):
        data=request.get('tx',[])
        if len(data)<5 or data[4]!=3:
            return PmodReadOnlyTransport.validate(request)
        if request.get('op')!='exchange' or request.get('decision_owner')!='PYNQ':
            raise ValueError('Board-owned exchange required')
        if any(type(x)is not int or not 0<=x<=255 for x in data):raise ValueError('Invalid byte')
        tx=bytes(data)
        if (len(tx)<8 or tx[:3]!=b'\xff\xff\x01' or tx[3]+4!=len(tx) or
            sum(tx[2:])%256!=255 or request.get('minimum_rx')!=6):raise ValueError('Invalid WRITE')
        addr,v=tx[5],tx[6:-1]
        if addr==40 and v==b'\0':return tx,6
        if not self.armed or self.trial is None:raise ValueError('Pan session not armed')
        t=self.trial;cfg,ram=t['cfg'],t['ram'];ok=False
        if addr==42 and len(v)==6:
            ok=t['rebased'] and t['low']<=signed(v)<=t['high'] and word(v,2)==0 and word(v,4)==t['speed']
        elif addr==41 and len(v)==1:
            ok=v==bytes([t['acceleration']]) or (self.torque_off and v==ram[1:2])
        elif addr==48 and len(v)==2:
            ok=word(v)==min(200,word(ram,8)) or (self.torque_off and v==ram[8:10])
        elif addr==9:ok=self.torque_off and v in (b'\0'*4,cfg[9:13])
        elif addr==18:ok=self.torque_off and v in (cfg[18:19],bytes([cfg[18]|16]))
        elif addr in (44,46):ok=self.torque_off and v==ram[addr-40:addr-38]
        if not ok:raise ValueError('Write exceeds session profile, one-turn bounds or restoration values')
        return tx,6
