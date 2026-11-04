# V2 candidate: explicit acceleration 2/5/10, speed600 unchanged.
# Not validated on hardware; original V1 remains the accepted controller.
"""PYNQ-owned ST3215 protocol, motion decisions and arrival verification.

The injected exchange function transports complete bytes only. Windows never
constructs a goal, direction, speed or servo packet. All angles remain in the
fixed array frame until a measured yaw calibration supplies the mapping.
"""
import math
import time


def packet(op, params=()):
    values=[1,len(params)+2,op]+list(params)
    if any(type(x)is not int or not 0<=x<=255 for x in values):
        raise ValueError('Invalid protocol bytes')
    return bytes([255,255]+values+[255-(sum(values)&255)])


def response(raw,tx,count):
    raw=bytes(raw);found=[]
    for i in range(len(raw)-5):
        if raw[i:i+2]!=b'\xff\xff':continue
        n=raw[i+3]+4;f=raw[i:i+n]
        if len(f)!=n or n!=count+6 or f==tx:continue
        if f[2]!=1 or (sum(f[2:])&255)!=255:continue
        found.append(f)
    if len(found)!=1:raise ValueError('Expected exactly one valid servo reply')
    if found[0][4]:raise ValueError('Servo status error %d'%found[0][4])
    return found[0][5:-1]


def word(data,offset=0):return data[offset]+256*data[offset+1]


def signed(data,offset=0):
    v=word(data,offset);return -(v&32767) if v&32768 else v


def word_bytes(value):
    if type(value)is not int or not 0<=value<=65535:raise ValueError('Word range')
    return [value&255,value>>8]


def signed_bytes(value):
    if type(value)is not int or abs(value)>32767:raise ValueError('Signed range')
    return word_bytes((-value)|32768 if value<0 else value)


def shortest_target(current,goal_single):
    if type(current)is not int or abs(current)>32767:raise ValueError('Current position range')
    if type(goal_single)is not int or not 0<=goal_single<4096:raise ValueError('Goal range')
    delta=(goal_single-current+2048)%4096-2048
    goal=current+delta
    if abs(goal)>32767:raise ValueError('Target overflow')
    return goal


def arrived(feedback,target):
    return abs(feedback['position']-target)<=4 and feedback['speed']==0 and feedback['moving']==0


def bearing_goal(event,calibration):
    """No implicit sign, zero, identity pairing or use of a known target angle."""
    if calibration.get('status')!='MEASURED_YAW_ALIGNMENT':raise ValueError('Yaw calibration pending')
    if event.get('coordinate_frame')!='ARRAY_FIXED' or event.get('status')!='VALID_BEARING':raise ValueError('Unusable bearing')
    if not calibration.get('evidence') or calibration.get('servo_sign') not in (-1,1):raise ValueError('Missing measured alignment')
    bearing=float(event['bearing_deg']);zero=float(calibration['array_bearing_at_servo_zero_deg'])
    if not math.isfinite(bearing+zero):raise ValueError('Nonfinite bearing')
    value=(bearing-zero)*calibration['servo_sign']*4096/360
    return {'decision_owner':'PYNQ','source':'MEASURED_YAW_ALIGNMENT','bearing_deg':bearing,
        'target_single':round(value)%4096,'speed':600,'acceleration':2,'torque_limit_max':200,
        'calibration_evidence':calibration['evidence']}


class BoardServo:
    def __init__(self,exchange,clock=time.monotonic,sleep=time.sleep):
        self.transport=exchange;self.clock=clock;self.sleep=sleep
        self.seq=0;self.events=[];self.feedback=[];self.full=False;self.prepared=False

    def command(self,op,**kw):
        self.seq+=1;r=dict(op=op,seq=self.seq,decision_owner='PYNQ',**kw)
        begin=self.clock();answer=self.transport(r);end=self.clock()
        if answer.get('seq')!=self.seq:raise ValueError('Relay sequence mismatch')
        self.events.append(dict(request=r,response=answer,board_begin=begin,board_end=end))
        if answer.get('error'):raise IOError(answer['error'])
        return answer

    def exchange(self,name,tx,count):
        reply=self.command('exchange',name=name,tx=list(tx),minimum_rx=count+6)
        if reply.get('tx')!=list(tx):raise ValueError('Relay changed outgoing bytes')
        if reply.get('stale_rx'):raise ValueError('Stale serial bytes')
        return response(reply['rx'],tx,count)

    def read(self,name,address,count):return self.exchange(name,packet(2,[address,count]),count)

    def write(self,name,address,data):
        data=list(data)
        sizes={9:4,18:1,40:1,41:1,42:6,44:2,46:2,48:2}
        if address not in sizes or len(data)!=sizes[address]:raise ValueError('Unsupported register write')
        if address==40:
            if data!=[0]:raise ValueError('Explicit torque enable not used')
        elif not self.prepared:raise ValueError('Fresh board preflight required')
        return self.exchange(name,packet(3,[address]+list(data)),0)

    def telemetry(self,name):
        d=self.read(name,56,15)
        r=dict(name=name,monotonic=self.clock(),position=signed(d),speed=signed(d,2),
            load_raw=word(d,4),voltage_v=d[6]/10,temperature_c=d[7],async_flag=d[8],
            status=d[9],moving=d[10],internal_target=signed(d,11),current_raw=word(d,13),full_angle=self.full)
        self.feedback.append(r)
        if r['async_flag'] or r['status'] or not 10.5<=r['voltage_v']<=12.6 or r['temperature_c']>55 or r['current_raw']>100:
            raise ValueError('Servo feedback guard')
        if not self.full and not 0<=r['position']<4096:raise ValueError('Single turn feedback out of range')
        return r

    def preflight(self):
        self.exchange('ping',packet(1),0)
        cfg=self.read('configuration_before',0,40);ram=self.read('sram_before',40,10);lk=self.read('lock_before',55,1)
        if not (cfg[2]==0 and cfg[5]==1 and cfg[6]==0 and cfg[8]==1 and cfg[33]==0 and cfg[30]==1 and cfg[18]==12 and word(cfg,9)==0 and word(cfg,11)==4095 and ram[0]==0 and lk==b'\x01'):
            raise ValueError('Unexpected configuration or torque')
        first=self.telemetry('preflight_0');self.sleep(.15);last=self.telemetry('preflight_1')
        if first['moving'] or last['moving'] or first['speed'] or last['speed'] or abs((last['position']-first['position']+2048)%4096-2048)>2:
            raise ValueError('Servo is not stationary')
        limit=min(200,word(ram,8))
        if limit<50 or word(ram,8)>1000:raise ValueError('Unexpected torque limit')
        self.cfg,self.ram,self.limit=cfg,ram,limit
        self.prepared=True
        self.initial=last
        return dict(status='BOARD_READ_ONLY_PASS',decision_owner='PYNQ',position=last['position'],torque=ram[0],feedback=last)

    def off_check(self,name):
        if self.read(name,40,1)!=b'\x00':raise ValueError('Torque off not confirmed')

    def goal(self,name,value,speed):
        data=signed_bytes(value)+[0,0]+word_bytes(speed)
        self.write(name,42,data)
        if self.read(name+'_readback',42,6)!=bytes(data):raise ValueError('Goal readback mismatch')

    def point(self,plan):
        if plan.get('decision_owner')!='PYNQ' or plan.get('source') not in ('MEASURED_YAW_ALIGNMENT','BOARD_CALIBRATION_STEP','MEASURED_FIXED_SOURCE_ASSOCIATION'):
            raise ValueError('Board decision provenance missing')
        target_single=plan['target_single'];shortest_target(0,target_single)
        if plan.get('speed')!=600:raise ValueError('Unverified speed')
        acceleration=plan.get('acceleration',2)
        if type(acceleration) is not int or acceleration not in (2,5,10):raise ValueError('Unsupported acceleration')
        result={'decision_owner':'PYNQ','plan':plan,'status':'FAILED','cleanup_verified':False,'cleanup_errors':[]}
        self.preflight();cfg,ram=self.cfg,self.ram
        self.command('arm_cleanup',tx=list(packet(3,[40,0])),minimum_rx=6)
        wrote=False;off=False;started=self.clock()
        try:
            wrote=True
            self.write('limit_torque',48,word_bytes(self.limit));self.write('bounded_acceleration',41,[acceleration])
            self.write('multiturn_limits',9,[0,0,0,0]);self.off_check('off_after_limits')
            self.write('full_feedback',18,[cfg[18]|16]);self.full=True;self.off_check('off_after_phase')
            expected=bytearray(cfg);expected[9:13]=b'\x00'*4;expected[18]|=16
            if self.read('configuration_multiturn',0,40)!=expected or self.read('lock_multiturn',55,1)!=b'\x01':raise ValueError('Config readback')
            first=self.telemetry('full_angle_start')
            if abs((first['position']-self.initial['position']+2048)%4096-2048)>2 or first['speed'] or first['moving']:raise ValueError('Coordinate changed')
            start=first['position'];target=shortest_target(start,target_single)
            result.update(start_full=start,target_full=target,target_single=target_single)
            self.goal('preload_live_position',start,600)
            armed=self.read('sram_armed',40,10)
            if not (armed[0]==1 and armed[1]==acceleration and signed(armed,2)==start and word(armed,4)==0 and word(armed,6)==600 and word(armed,8)==self.limit):raise ValueError('Armed readback')
            hold=self.telemetry('hold_start')
            if not arrived(hold,start):raise ValueError('Hold failed')
            direction=1 if target>=start else -1
            result['goal_issued_monotonic']=self.clock();self.goal('point_goal',target,600)
            stable=0;anchor=last=hold;near=None
            while self.clock()-started<30:
                f=self.telemetry('turn_%04d'%len(self.feedback));dt=f['monotonic']-last['monotonic']
                if not min(start,target)-4<=f['position']<=max(start,target)+4 or abs(f['speed'])>900:raise ValueError('Travel guard')
                if (f['position']-last['position'])*direction < -4 or abs(f['position']-last['position'])>max(128,1200*dt):raise ValueError('Feedback discontinuity')
                if f['monotonic']-anchor['monotonic']>3 and abs(target-f['position'])>16:
                    if abs(f['position']-anchor['position'])<4:raise ValueError('No progress')
                    anchor=f
                if abs(target-f['position'])<=16:
                    if near is None:near=self.clock()
                    if self.clock()-near>8:raise ValueError('Endpoint did not meet original tolerance')
                stable=stable+1 if arrived(f,target) else 0
                if stable>=3:
                    result.update(status='BOARD_POINT_PASSED',arrival_monotonic=self.clock(),residual_counts=target-f['position']);break
                last=f;self.sleep(.1)
            if result['status']!='BOARD_POINT_PASSED':raise TimeoutError('Point deadline')
        except BaseException as exc:result['error']=repr(exc)
        finally:
            if wrote:
                try:self.write('torque_disable',40,[0]);self.off_check('off_verified');off=True
                except BaseException as exc:result['cleanup_errors'].append('off:'+repr(exc))
                if off:
                    try:
                        stop=self.telemetry('disabled')
                        if stop['moving'] or stop['speed']:
                            self.sleep(.3);stop=self.telemetry('disabled_settle')
                            if stop['speed'] or stop['moving']:raise ValueError('Residual motion')
                        self.write('restore_phase',18,[cfg[18]]);self.full=False;self.off_check('off_after_restore_phase')
                        self.write('restore_limits',9,cfg[9:13]);self.off_check('off_after_restore_limits')
                        self.write('restore_acceleration',41,[ram[1]])
                        for address in (44,46,48):self.write('restore_%d'%address,address,ram[address-40:address-38])
                        after=self.read('sram_after',40,10)
                        if any(after[j]!=ram[j] for j in (0,1,4,5,6,7,8,9)):raise ValueError('SRAM restore')
                        if self.read('configuration_after',0,40)!=cfg or self.read('lock_after',55,1)!=b'\x01':raise ValueError('Config restore')
                        result['final_feedback']=self.telemetry('final_disabled')
                        result['cleanup_verified']=True
                        self.command('disarm_cleanup')
                    except BaseException as exc:result['cleanup_errors'].append('restore:'+repr(exc))
            result['finished_monotonic']=self.clock()
        return result
