"""Board command: default STOP; a bounded beep needs verified physical wiring.

STOP preserves the PL's existing arm/polarity/mode and clears its duration.
It never converts a floating, unverified connection into an assumed silence.
"""
import argparse,json,time
from pathlib import Path
from buzzer_control_V2 import Buzzer,ADDRESS


def stop_control(io,clock=time.monotonic,sleep=time.sleep):
    before=io.read(8)
    if before>>16!=0x425a:raise ValueError('BUZZER_ABI_MISMATCH')
    command=io.read(0);io.write(0,command & 0xffff0000)
    end=clock()+.02
    while True:
        value=io.read(8)
        if not(value&2) and (not(value&1) or bool(value&16)==bool(command&(1<<30))):break
        if clock()>=end:raise TimeoutError('BUZZER_STOP_READBACK_FAILED')
        sleep(.001)
    return dict(status='PL_STOP_CONFIRMED',command_before=command,command_after=io.read(0),
                status_word=value,pin_armed=bool(value&1),audible_silence_verified=False)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['status','stop','beep'],nargs='?',default='stop')
    p.add_argument('--milliseconds',type=int,default=150)
    p.add_argument('--wiring',type=Path)
    p.add_argument('--pwm',action='store_true')
    args=p.parse_args(argv)
    # A beep must pass evidence checks before importing/opening the hardware.
    wiring=None
    if args.action=='beep':
        if args.wiring is None:p.error('--wiring is required for beep')
        wiring=json.loads(args.wiring.read_text())
        from buzzer_control_V2 import validate_wiring
        validate_wiring(wiring)
        if not 1<=args.milliseconds<=1000:p.error('--milliseconds must be 1..1000')
    from pynq import PL,MMIO
    if PL.ip_dict.get('buzzer_gpio_0',{}).get('phys_addr')!=ADDRESS:
        raise RuntimeError('BUZZER_OVERLAY_NOT_LOADED_NO_OUTPUT_WRITTEN')
    io=MMIO(ADDRESS,65536)
    if io.read(8)>>16!=0x425a:raise ValueError('BUZZER_ABI_MISMATCH')
    if args.action=='status':result=dict(status_word=io.read(8),command=io.read(0),output_written=False)
    elif args.action=='stop':result=stop_control(io)
    else:
        buzzer=Buzzer(io,wiring)
        try:
            buzzer.arm_silent(pwm=args.pwm)
            result=buzzer.beep(args.milliseconds)
        finally:stop_control(io)
    print(json.dumps(result,sort_keys=True))


if __name__=='__main__':main()
