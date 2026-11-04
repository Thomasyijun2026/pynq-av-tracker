"""Direct UART transport for one bounded, board-owned small-angle trial.

V1 remains read-only. The existing BoardServo supplies protocol and arrival
decisions; this addition supports its cleanup handshake and constrained writes.
"""
from servo_pmod_transport_V1 import PmodReadOnlyTransport
from servo_board_V3 import packet, response, signed, word


class PmodTrialTransport(PmodReadOnlyTransport):
    def __init__(self, *args, **kwargs):
        self.trial = None
        self.armed = False
        self.torque_off = True
        super().__init__(*args, **kwargs)

    def configure_trial(self, configuration, sram, start, span=57):
        if self.armed or self.trial is not None:
            raise ValueError('Trial already configured')
        cfg, ram = bytes(configuration), bytes(sram)
        if len(cfg) != 40 or len(ram) != 10 or ram[0] != 0:
            raise ValueError('Fresh torque-off preflight required')
        if type(start) is not int or not 64 <= start <= 4031 or span != 57:
            raise ValueError('Only the bounded 57-count trial is supported')
        self.trial = dict(cfg=cfg, ram=ram, start=start, low=start-57, high=start+57)

    def validate(self, request):
        data = request.get('tx', [])
        if len(data) < 5 or data[4] != 3:
            return PmodReadOnlyTransport.validate(request)
        if request.get('op') != 'exchange' or request.get('decision_owner') != 'PYNQ':
            raise ValueError('Board-owned exchange required')
        if any(type(x) is not int or not 0 <= x <= 255 for x in data):
            raise ValueError('Invalid byte')
        tx = bytes(data)
        if (len(tx) < 8 or tx[:3] != b'\xff\xff\x01' or tx[3]+4 != len(tx)
                or sum(tx[2:]) % 256 != 255 or request.get('minimum_rx') != 6):
            raise ValueError('Invalid WRITE packet')
        address, value = tx[5], tx[6:-1]
        if address == 40 and value == b'\x00':
            return tx, 6  # Emergency torque-off is available even after a fault.
        if not self.armed or self.trial is None:
            raise ValueError('Motion trial not armed')
        cfg, ram = self.trial['cfg'], self.trial['ram']
        allowed = False
        if address == 42 and len(value) == 6:
            target = signed(value)
            allowed = (self.trial['low'] <= target <= self.trial['high']
                       and word(value, 2) == 0 and word(value, 4) == 600)
        elif address == 48 and len(value) == 2:
            allowed = (50 <= word(value) <= 200 or
                       (self.torque_off and value == ram[8:10]))
        elif address == 41 and len(value) == 1:
            allowed = value == b'\x02' or (self.torque_off and value == ram[1:2])
        elif address == 9:
            allowed = self.torque_off and value in (b'\x00'*4, cfg[9:13])
        elif address == 18:
            allowed = self.torque_off and value in (cfg[18:19], bytes([cfg[18] | 16]))
        elif address in (44, 46):
            allowed = self.torque_off and value == ram[address-40:address-38]
        if not allowed:
            raise ValueError('Write outside bounded trial or restoration values')
        return tx, 6

    def __call__(self, request):
        op = request.get('op')
        if op in ('arm_cleanup', 'disarm_cleanup'):
            if self.closed or request.get('decision_owner') != 'PYNQ':
                raise ValueError('Invalid cleanup ownership')
            if op == 'arm_cleanup':
                if (self.armed or self.trial is None or
                        request.get('tx') != list(packet(3, [40, 0])) or
                        request.get('minimum_rx') != 6):
                    raise ValueError('Invalid cleanup arm')
                self.armed = True
            else:
                if not self.armed or not self.torque_off:
                    raise ValueError('Cannot disarm before torque-off acknowledgement')
                self.armed = False
            return dict(seq=request['seq'], op=op, error=None, decision_owner='PYNQ')
        tx, _ = self.validate(request)
        # A goal WRITE may engage torque even when its reply is lost.
        if tx[4] == 3 and tx[5] == 42:
            self.torque_off = False
        answer = super().__call__(request)
        if tx[4] == 3 and tx[5] == 40 and not answer.get('error'):
            response(answer['rx'], tx, 0)
            self.torque_off = True
        return answer

    def close(self):
        emergency = None
        if self.armed and not self.closed:
            tx = packet(3, [40, 0])
            request = dict(op='exchange', seq=-1, name='close_torque_off',
                           decision_owner='PYNQ', tx=list(tx), minimum_rx=6)
            reply = self(request)
            if reply.get('error'):
                raise IOError('Emergency torque-off unconfirmed: '+reply['error'])
            tx_read = packet(2, [40, 1])
            reply_read = self(dict(op='exchange', seq=-2, name='close_off_readback',
                                   decision_owner='PYNQ', tx=list(tx_read), minimum_rx=7))
            if reply_read.get('error') or response(reply_read['rx'], tx_read, 1) != b'\x00':
                raise IOError('Emergency torque-off readback unconfirmed')
            emergency = dict(torque_off_acknowledged=True, torque_off_readback=True)
            self.armed = False
        result = super().close()
        result['emergency_cleanup'] = emergency
        return result
