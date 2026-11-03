from collections import Counter
import json
from pathlib import Path
import tempfile
import unittest

from truth_dataset import clean,flatten,generate,geometry_hash,legal_type_word,planted,read,shuffled,verify_route
from truth_benchmark import SOLVERS,audit_dataset,classify,run,solver_command,summarize


def fixture():
    levels={}
    for i in range(18):
        layer=1+i//3;x=(i%3)*8
        levels.setdefault(str(layer),[]).append({'id':f'{layer}-{x}-0','rolNum':x,'rowNum':0,'type':i//3+1})
    return {'levelData':levels}


class TruthBenchmarkTests(unittest.TestCase):
    def test_planted_maps_preserve_geometry_counts_and_legal_complete_witness(self):
        source=fixture();counts=Counter(c['type'] for c in flatten(source))
        for seed in range(12):
            for cap in (4,5,6):
                data,route,metrics=planted(source,seed,cap,('random','deep','broad')[seed%3])
                self.assertEqual(geometry_hash(source),geometry_hash(data))
                self.assertEqual(Counter(c['type'] for c in flatten(data)),counts)
                self.assertLessEqual(metrics['peak_tray'],cap)
                self.assertEqual(metrics['eliminations'],6)
                self.assertEqual(data,planted(source,seed,cap,('random','deep','broad')[seed%3])[0])

    def test_shuffling_preserves_multiset_without_assigning_a_truth_label(self):
        data=shuffled(fixture(),77)
        self.assertEqual(geometry_hash(data),geometry_hash(fixture()))
        self.assertEqual(Counter(c['type'] for c in flatten(data)),Counter(c['type'] for c in flatten(fixture())))
        self.assertEqual(set(data),{'levelData'})

    def test_independent_validator_rejects_incomplete_covered_and_overflow_routes(self):
        data,route,_=planted(fixture(),19,6,'random')
        with self.assertRaises(ValueError):verify_route(data,route[:-1])
        with self.assertRaises(ValueError):verify_route(data,list(reversed(route)))
        chain={'levelData':{str(12-i):[{'id':str(i),'type':i%4+1,'rolNum':0,'rowNum':0}] for i in range(12)}}
        with self.assertRaisesRegex(ValueError,'overflow'):verify_route(chain,[str(i) for i in range(12)])

    def test_manifest_witness_separation_and_input_tamper_detection(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source.json';source.write_text(json.dumps(fixture()))
            dataset=root/'dataset';m=generate(source,dataset,count=2,seed=71)
            self.assertEqual(len(m['cases']),5);audit_dataset(dataset)
            for c in m['cases']:
                self.assertEqual(set(read(dataset/c['map'])),{'levelData'})
                for solver in SOLVERS:
                    command=solver_command(solver,dataset/c['map'],root/'job',1)
                    self.assertFalse(any('witness' in arg or 'manifest' in arg or arg=='--hint' for arg in command))
            with self.assertRaises(FileExistsError):generate(source,dataset,count=2)
            p=dataset/m['cases'][0]['map'];p.write_text('{}')
            with self.assertRaises(ValueError):audit_dataset(dataset)

    def test_unknown_and_unverified_unsat_do_not_become_certified_negative_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory);(out/'solver_result.json').write_text('{"state":"infeasible"}')
            unknown={'truth':'unknown'};positive={'truth':'solvable'}
            self.assertEqual(classify('cp_sat',positive,fixture(),out,2)['state'],'contradiction')
            result=classify('cp_sat',unknown,fixture(),out,2)
            self.assertEqual(result['state'],'model_unsat');self.assertFalse(result['certified_unsat'])
            result=classify('cp_sat',unknown,fixture(),out,-1,'hard_wall_limit')
            self.assertEqual(result['state'],'unknown');self.assertFalse(result['unsat_claim'])

    def test_cross_solver_results_count_each_random_map_once_in_truth_bounds(self):
        m={'cases':[{'id':'a','cohort':'shuffled'},{'id':'b','cohort':'shuffled'}]}
        rows=[{'case_id':'a','cohort':'shuffled','solver':s,'state':'solved','solver_seconds':1} for s in SOLVERS]
        summary=summarize(m,rows,{'solvers':SOLVERS})
        bound=summary['sample_solvability_bounds'][0]
        self.assertEqual(bound['verified_solvable_cases'],1)
        self.assertEqual(bound['solvability_lower_bound'],.5);self.assertEqual(bound['solvability_upper_bound'],1)

    def test_small_end_to_end_suite_and_resume_use_no_solution_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source.json';source.write_text(json.dumps(fixture()))
            dataset=root/'dataset';generate(source,dataset,count=1,seed=81)
            summary=run(dataset,seconds=.5,parallel=2,memory_mb=512)
            self.assertEqual(summary['completed_runs'],12);self.assertEqual(summary['state'],'complete')
            self.assertEqual(summary['errors'],0)
            self.assertTrue((dataset/'REPORT.md').is_file());self.assertTrue((dataset/'report.html').is_file())
            again=run(dataset,seconds=.5,parallel=2,memory_mb=512)
            self.assertEqual(again['completed_runs'],12)
            for p in (dataset/'runs').glob('*/*/invocation.json'):
                self.assertFalse(read(p)['witness_passed'])


if __name__=='__main__':unittest.main()
