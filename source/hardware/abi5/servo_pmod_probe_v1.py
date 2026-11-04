"""One bounded board-owned read-only probe of the direct Pmod UART."""
import hashlib,json,os,time
from pathlib import Path
from pynq import Overlay,MMIO,Clocks,PL
from servo_pmod_transport_V1 import PmodReadOnlyTransport
from servo_board_V3 import BoardServo

def hardware_quiet(c,d):
    c.write(4,0)
    d.write(0x30,4)
    deadline=time.monotonic()+.5
    while d.read(0x30)&4:
        if time.monotonic()>deadline:raise TimeoutError('DMA reset did not finish')
        time.sleep(.001)
    r=dict(control=c.read(4),capture_status=c.read(8),dma_control=d.read(0x30),dma_status=d.read(0x34))
    r['verified']=(r['control']==0 and not(r['capture_status']&5) and not(r['dma_control']&1) and bool(r['dma_status']&1) and not(r['dma_status']&0x770))
    return r

def run_probe(root,expected):
    root=Path(root);result=dict(status='NOT_COMPLETED',boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
        audio_started=False,camera_opened=False,motion_commands=0,servo_register_writes=0,decision_owner='PYNQ',
        previous_bitfile=str(PL.bitfile_name),cleanup_verified=False)
    uart=None;c=None;d=None;b=None
    try:
        for name,digest in expected.items():
            if hashlib.sha256((root/name).read_bytes()).hexdigest()!=digest:raise ValueError('Source mismatch '+name)
        holders=[]
        for proc in Path('/proc').iterdir():
            if proc.name.isdigit():
                try:
                    for fd in (proc/'fd').iterdir():
                        try:
                            target=os.readlink(str(fd))
                            if target.startswith(('/dev/video','/dev/ttyUSB','/dev/ttyACM')):holders.append([proc.name,target])
                        except FileNotFoundError:pass
                except FileNotFoundError:pass
        result['holders_before']=holders
        if holders:raise RuntimeError('Hardware owned by another process')
        overlay=Overlay(str(root/'av_pmod.bit'),download=True)
        result['overlay_downloaded']=True;result['fclk0_mhz']=Clocks.fclk0_mhz
        if abs(result['fclk0_mhz']-100)>.001:raise ValueError('FCLK must be 100 MHz')
        for name,addr in [('mic_capture_0',0x43c00000),('axi_dma_0',0x40400000),('servo_uart_0',0x43c10000)]:
            if overlay.ip_dict[name]['phys_addr']!=addr:raise ValueError('HWH address mismatch '+name)
        c=MMIO(0x43c00000,65536);d=MMIO(0x40400000,65536)
        result['audio_abi']=[hex(c.read(0)),hex(c.read(0x40))]
        if result['audio_abi']!=['0x4d494301','0x444f4102']:raise ValueError('Audio ABI mismatch')
        result['quiet_before']=hardware_quiet(c,d)
        if not result['quiet_before']['verified']:raise RuntimeError('Audio hardware not quiet')
        uart=PmodReadOnlyTransport(MMIO(0x43c10000,65536));b=BoardServo(uart)
        result['servo']=b.preflight()
        result['status']='DIRECT_PMOD_READ_ONLY_PASS'
    except Exception as e:
        result['status']='DIRECT_PMOD_PROBE_FAILED';result['error']=repr(e)
        # Configuration guard failure must not suppress already received raw bytes.
        # No automatic retries and no register writes to the servo.
    finally:
        result['board_events']=b.events if b else []
        result['feedback']=b.feedback if b else []
        result['transport_events']=uart.events if uart else []
        try:
            if uart:result['uart_cleanup']=uart.close()
            elif result.get('overlay_downloaded'):
                u=MMIO(0x43c10000,65536)
                if u.read(0)==0x55525431:
                    u.write(4,256);result['uart_cleanup']=dict(uart_disabled_and_flushed=u.read(4)==0 and u.read(8)==2 and u.read(16)==0)
            if c is not None and d is not None:result['quiet_after']=hardware_quiet(c,d)
            result['cleanup_verified']=bool(result.get('uart_cleanup',{}).get('uart_disabled_and_flushed') and result.get('quiet_after',{}).get('verified'))
        except Exception as e:result['cleanup_error']=repr(e)
        result['completed_monotonic']=time.monotonic()
        (root/'board_result.json').write_text(json.dumps(result,indent=2))
    return result
