from concurrent.futures import Future
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import prop_plan
import workflow_controller as workflow
from static_plan import plan_current
from desktop_assistant import validate_execution
from solve_verified_map import atomic_json

def fixture():
    word=[1,2,3,4,5,6,1,1,2,2,3,3,4,4,5,5,6,6]
    return {'levelData':{str(18-i):[{'id':str(i),'type':t,'rolNum':0,'rowNum':0}] for i,t in enumerate(word)}}

def actions():
    result=[str(i) for i in range(6)]+['MOVE_OUT']
    for j in range(6):
        result.extend([str(6+j*2),str(7+j*2)])
        if j<3:result.append(str(j))
    return result

class PropTests(unittest.TestCase):
    def test_seventh_insert_is_legal_when_it_immediately_clears_a_triple(self):
        word=[1,1,2,3,4,5,1,2,2,3,3,4,4,5,5]
        data={'levelData':{str(15-i):[{'id':str(i),'type':t,'rolNum':0,'rowNum':0}] for i,t in enumerate(word)}}
        route=[str(i) for i in range(7)]+['MOVE_OUT','7','8','2','9','10','3','11','12','4','13','14']
        trace,peak=prop_plan.verify(data,route)
        self.assertEqual(len(trace[6]['tray_before']),6);self.assertEqual(len(trace[6]['tray_after']),4);self.assertEqual(peak,6)
        with self.assertRaisesRegex(ValueError,'满七张'):prop_plan.verify(fixture(),[str(i) for i in range(7)])

    def test_complete_replay_tampering_and_execution_gate(self):
        data=fixture();trace,peak=prop_plan.verify(data,actions())
        self.assertEqual(len(trace),22);self.assertLessEqual(peak,6)
        for bad in (actions()[:-1],actions()+['MOVE_OUT'],['0','0']+actions()[2:],list(reversed(actions()))):
            with self.assertRaises((ValueError,KeyError)):prop_plan.verify(data,bad)
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);mp=folder/'map.json';atomic_json(mp,data)
            _,answer=prop_plan.publish(mp,data,actions(),[],folder/'prop','test')
            self.assertTrue(answer['prop_validated']);self.assertFalse(answer['validated']);self.assertFalse(answer['executable'])
            with self.assertRaisesRegex(ValueError,'道具'):validate_execution(answer)
            self.assertIsNone(prop_plan.cached(mp,data,['wrong'],folder/'prop'))
            answer['map_sha256']='wrong';atomic_json(folder/'prop/solution.json',answer)
            self.assertIsNone(prop_plan.cached(mp,data,[],folder/'prop'))

    def test_native_search_generates_new_route_under_short_budget(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);mp=folder/'map.json';atomic_json(mp,fixture())
            result=prop_plan.search(mp,fixture(),[],folder/'prop',[[str(i) for i in range(18)]],seconds=2)
            self.assertIsNotNone(result);self.assertEqual(result[1]['move_out_uses'],1)
            prop_plan.verify(fixture(),result[1]['actions'])

    def test_cancel_and_deadline_reap_owned_children(self):
        for cancel_test in (False,True):
            processes=[]
            class Process:
                def __init__(self,*a,**kw):self.running=True;self.waited=False;processes.append(self)
                def poll(self):return None if self.running else -15
                def terminate(self):self.running=False
                def wait(self,timeout):self.waited=True;return -15
            with tempfile.TemporaryDirectory() as d:
                folder=Path(d);mp=folder/'map.json';atomic_json(mp,fixture());start=time.monotonic()
                with patch('prop_plan.subprocess.Popen',side_effect=Process),patch('search_resources.ResourceSampler.process_memory',return_value=(0,0)):
                    if cancel_test:
                        with self.assertRaisesRegex(ValueError,'暂停'):prop_plan.search(mp,fixture(),[],folder/'prop',[[str(i) for i in range(18)]],seconds=1,cancel=lambda:bool(processes))
                    else:self.assertIsNone(prop_plan.search(mp,fixture(),[],folder/'prop',[[str(i) for i in range(18)]],seconds=.15))
                self.assertLess(time.monotonic()-start,1);self.assertTrue(processes);self.assertTrue(all(p.waited and not p.running for p in processes))

    def test_controller_retains_prop_result_and_never_prepares_player(self):
        with tempfile.TemporaryDirectory() as d,patch.object(workflow,'OUT',Path(d)):
            controller=workflow.Controller();controller.auto=True;controller.job_kind='original';controller.job=Future()
            controller.job.set_result({'prop_route_ready':True,'solution_path':'prop.json','message':'一次移出已验证'})
            try:
                controller.collect_job();self.assertEqual(controller.state,'prop_ready');self.assertFalse(controller.auto);self.assertIsNone(controller.ready)
                with self.assertRaisesRegex(ValueError,'道具'):controller.start_player()
                controller.command('refresh');self.assertIsNone(controller.prop_result)
            finally:controller.pool.shutdown(wait=True)

    def test_static_fallback_never_publishes_normal_answer(self):
        data={'levelData':{'1':[{'id':str(i),'type':i//3+1,'rolNum':i*8,'rowNum':0} for i in range(66)]}}
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);mp=folder/'map.json';atomic_json(mp,data);fake={'execution_kind':prop_plan.KIND,'operation_count':70}
            def search(*args,**kw):
                self.assertLessEqual(kw['seconds'],20);return folder/'prop.json',fake
            with patch('static_accelerator.prepare',return_value=({'state':'unknown'},None,[],None)),patch('static_plan.solve_board',return_value=({'state':'unknown'},None)),patch('capacity_probe.start',side_effect=OSError()),patch('prop_plan.search',side_effect=search):
                _,answer=plan_current(mp,[],folder/'out',seconds=60)
            self.assertEqual(answer['execution_kind'],prop_plan.KIND);self.assertFalse((folder/'out/complete/solution.json').exists())
            status=prop_plan.read(folder/'out/search_status.json');self.assertEqual(status['state'],'prop_ready');self.assertFalse(status['executable'])

if __name__=='__main__':unittest.main()
