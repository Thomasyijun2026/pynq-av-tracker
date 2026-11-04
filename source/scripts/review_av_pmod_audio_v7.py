"""Independent built bit/HWH/report review; does not touch hardware."""
import argparse,json,re,xml.etree.ElementTree as E
from pathlib import Path
import av_overnight_V1 as a


def main():
    parser=argparse.ArgumentParser();parser.add_argument('evidence');args=parser.parse_args()
    ev=Path(args.evidence);info=a.read(ev/'summary.json');b=Path(info['run']);rp=b/'reports';checks={}
    def require(name,condition):
        checks[name]=bool(condition)
        if not condition:raise AssertionError(name)
    require('build_completed',info['status']=='BUILD_PASS_PENDING_REVIEW' and info['exit_code']==0)
    for name,digest in info['sources'].items():require('source:'+name,a.sha(a.ROOT/name)==digest)
    require('release_preserved',a.sha(a.ROOT/'release/mic_localize_V2.zip')==a.ZIP_SHA)
    for name,digest in info['outputs'].items():require(name,a.sha(b/'output'/name)==digest)
    require('all_outputs',all(name in info['outputs'] for name in ['av_pmod.bit','av_pmod.hwh','av_pmod.xsa']))
    require('report_completion','AV_PMOD_AUDIO_V7_BUILD_PASS' in (ev/'console.log').read_text(errors='replace'))
    metrics=dict(x.split() for x in (rp/'timing_metrics.tsv').read_text().splitlines())
    require('setup',float(metrics['setup_slack_ns'])>=0);require('hold',float(metrics['hold_slack_ns'])>=0)
    timing=(rp/'check_timing.rpt').read_text();drc=(rp/'drc.rpt').read_text();cdc=(rp/'cdc.rpt').read_text()
    for field in ('no_clock','unconstrained_internal_endpoints','multiple_clock','loops','partial_input_delay','partial_output_delay','latch_loops'):
        require(field,re.search(r'checking '+field+r' \(0\)',timing) is not None)
    require('drc_no_severe',not re.search(r'\|\s*(Error|Critical Warning)\s*\|',drc))
    require('async_cdc_safe',re.search(r'False Path\s+6\s+6\s+0\s+0\s+0',cdc) is not None)
    require('mic_cdc_timed',re.search(r'None\s+4\s+4\s+0\s+0\s+0',cdc) is not None)
    io=(rp/'io.rpt').read_text()
    for port,pin in [('btn[0]','D19'),('btn[1]','D20'),('btn[2]','L20'),('btn[3]','L19'),('servo_tx','W14'),('servo_rx','Y14'),('mic_bclk','W8'),('mic_ws','Y8'),('buzzer_io','W19')]:
        require('pin:'+port,re.search(r'\|\s*'+pin+r'\s*\|\s*'+re.escape(port)+r'\s*\|[^\n]*LVCMOS33',io) is not None)
    xml=E.parse(b/'output/av_pmod.hwh');modules={m.get('INSTANCE'):m for m in xml.findall('.//MODULE')}
    params={p.get('NAME'):p.get('VALUE') for p in modules['buzzer_gpio_0'].findall('./PARAMETERS/PARAMETER')}
    for k,v in [('C_GPIO_WIDTH','32'),('C_GPIO2_WIDTH','32'),('C_ALL_OUTPUTS','1'),('C_ALL_INPUTS_2','1'),('C_IS_DUAL','1'),('C_DOUT_DEFAULT','0x00000000')]:
        actual=params.get(k)
        require(k,int(actual,0)==int(v,0) if actual is not None else False)
    addresses={x.get('INSTANCE'):int(x.get('BASEVALUE'),16) for x in xml.findall('.//MEMRANGE') if x.get('BASEVALUE')}
    for name,address in [('buzzer_gpio_0',0x43c30000),('buttons_0',0x43c20000),('servo_uart_0',0x43c10000),('mic_capture_0',0x43c00000),('axi_dma_0',0x40400000)]:require('address:'+name,addresses[name]==address)
    for name in ['gpio_io_o','gpio2_io_i']:
        require('buzzer_connection:'+name,modules['buzzer_gpio_0'].find(".//PORT[@NAME='"+name+"']/CONNECTIONS/CONNECTION[@INSTANCE='buzzer_0']") is not None)
    resources={};util=(rp/'utilization.rpt').read_text()
    for key,label in [('lut','Slice LUTs'),('ff','Slice Registers'),('bram','Block RAM Tile'),('dsp','DSPs')]:
        match=re.search(r'\|\s*'+re.escape(label)+r'\s*\|\s*([\d.]+)\s*\|',util);require('resource:'+key,match is not None);resources[key]=float(match.group(1))
    warnings=sorted(set(re.findall(r'\|\s*([A-Z]+[A-Z0-9]*-\d+)\s*\|\s*Warning\s*\|',drc)))
    result=dict(status='PASS_IMPLEMENTATION_REVIEW',scope='IMPLEMENTATION_ONLY_BOARD_NOT_RUN',build=str(b),outputs=info['outputs'],checks=checks,timing=metrics,resources=resources,retained_warnings=warnings)
    a.dump(ev/'review.json',result);print(json.dumps(dict(status=result['status'],checks=len(checks),timing=metrics,resources=resources,warnings=warnings)))


if __name__=='__main__':main()
