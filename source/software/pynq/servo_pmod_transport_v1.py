"""Board-local AXI UART transport, initially restricted to PING/READ packets.

No dependency on Windows, USB serial, or a network byte relay. The caller supplies
the already verified UART MMIO; construction resets this peripheral only.
"""
import time

class PmodReadOnlyTransport:
    def __init__(self, mmio, clock=time.monotonic, sleep=time.sleep):
        self.io=mmio;self.clock=clock;self.sleep=sleep;self.events=[];self.closed=False
        if self.io.read(0)!=0x55525431 or self.io.read(20)!=100:
            raise ValueError('Unexpected UART ABI or baud divisor')
        self.io.write(4,0x103)
        if self.io.read(4)!=3 or self.io.read(16)!=0:
            raise IOError('UART initialization readback')

    @staticmethod
    def validate(request):
        if request.get('op')!='exchange' or request.get('decision_owner')!='PYNQ':
            raise ValueError('Board-owned exchange required')
        data=request.get('tx',[])
        if any(type(x)is not int or not 0<=x<=255 for x in data):raise ValueError('Invalid byte')
        tx=bytes(data);minimum=request.get('minimum_rx')
        if len(tx)<6 or tx[:3]!=b'\xff\xff\x01' or tx[3]+4!=len(tx) or sum(tx[2:])%256!=255:
            raise ValueError('Invalid packet')
        if tx[4]==1:
            if len(tx)!=6 or minimum!=6:raise ValueError('PING shape')
        elif tx[4]==2:
            if len(tx)!=8 or not 1<=tx[6]<=64 or tx[5]+tx[6]>256 or minimum!=tx[6]+6:raise ValueError('READ shape')
        else:raise ValueError('Read-only transport rejects servo write/motion')
        return tx,minimum

    def drain(self):
        data=[]
        for _ in range(256):
            if not self.io.read(8)&1:return data
            data.append(self.io.read(12)&255)
        if self.io.read(8)&1:raise IOError('Unbounded incoming serial data')
        return data

    def __call__(self, request):
        # Validation precedes every hardware write, including malformed requests.
        tx,minimum=self.validate(request)
        if self.closed:raise RuntimeError('Transport closed')
        start=self.clock();r=dict(seq=request['seq'],tx=list(tx),rx=[],stale_rx=[],error=None,
            transport='PYNQ_AXI_UART',decision_owner='PYNQ',begin_monotonic=start)
        try:
            r['status_before']=self.io.read(8);r['stale_rx']=self.drain()
            if r['stale_rx']:raise IOError('Stale RX before command')
            if r['status_before']&0x7c:raise IOError('UART not idle or sticky error before command')
            for value in tx:
                if not self.io.read(8)&2:raise IOError('TX FIFO full')
                self.io.write(12,value)
            last_rx=None
            while self.clock()-start<0.15:
                status=self.io.read(8)
                if status&0x38:raise IOError('UART overflow or framing error')
                chunk=self.drain()
                if chunk:r['rx'].extend(chunk);last_rx=self.clock()
                if len(r['rx'])>128:raise IOError('Unexpected serial reply size')
                counts=self.io.read(16)
                if len(r['rx'])>=minimum and last_rx is not None and self.clock()-last_rx>=.002 and not(status&0x44) and counts==0:
                    break
                self.sleep(.0001)
            else:raise TimeoutError('No complete idle servo reply within 150ms')
        except Exception as e:r['error']=repr(e)
        finally:
            r['end_monotonic']=self.clock();r['status_after']=self.io.read(8);r['counts_after']=self.io.read(16)
            self.events.append(r)
        return r

    def close(self):
        if not self.closed:
            self.io.write(4,0x100);self.closed=True
        result=dict(control=self.io.read(4),status=self.io.read(8),counts=self.io.read(16))
        result['uart_disabled_and_flushed']=(result['control']==0 and result['counts']==0 and result['status']==2)
        return result
