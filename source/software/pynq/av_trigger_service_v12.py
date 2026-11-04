"""Independent candidate: aligned state requires the full-turn evidence contract."""
from av_trigger_service_V11 import Coordinator as Previous, ButtonEdges, make_handler, atomic_json
from av_init_proof_V1 import validate


class Coordinator(Previous):
    def _state(self):
        state=super()._state()
        state.update(version=12,initialization_evidence_contract='FULL_TURN_STATIC_AXIS_V1')
        return state

    def _accept(self,job,result,now):
        if job['action']=='initialize' and isinstance(result,dict) and result.get('navigation_ready') is True:
            proof=validate(result,self.boot_id,job['trigger_monotonic'],now)
            super()._accept(job,result,now)
            job['initialization_evidence_check']=proof
            return
        return super()._accept(job,result,now)
