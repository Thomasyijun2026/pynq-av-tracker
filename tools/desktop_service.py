"""Portable bootstrap for the unchanged Windows desktop server.
Only the current Windows output-device identity is supplied at runtime.
"""
import argparse,sys
from pathlib import Path
def main():
    p=argparse.ArgumentParser();p.add_argument('--workspace',required=True);p.add_argument('--out',required=True)
    a=p.parse_args();w=Path(a.workspace).resolve()
    sys.path[:0]=[str(w/'desktop/PYNQ_Control_V12'),str(w/'scripts'),str(w/'software/pynq')]
    import stimulus
    from pc_test_playback_V3 import endpoint
    with endpoint() as (read,_,__):stimulus.ENDPOINT=read()['endpoint_id']
    import server
    sys.argv=['server.py','--out',str(Path(a.out).resolve())]
    server.main()
if __name__=='__main__':main()
