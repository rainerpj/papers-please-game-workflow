import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
import workflow as w
import server


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / 'state.json'
        (self.root / 'evidence.md').write_text('Isolated test fixture; not customer evidence.', encoding='utf-8')
        w.atomic(self.path, w.seed())

    def apply(self, **data):
        data.setdefault('revision', w.read_json(self.path)['revision'])
        return w.apply(data, self.path, self.root)

    def evidence(self, kind='document', result='pass'):
        return dict(kind=kind, result=result, path='evidence.md', note='isolated test', by='test', at=w.now())

    def reviews(self):
        return {key: dict(result='pass', path='evidence.md', note='isolated review', by='test', at=w.now()) for key in ('correctness', 'necessity')}

    def complete(self, tid):
        task = next(t for t in w.read_json(self.path)['tasks'] if t['id'] == tid)
        return self.apply(op='update', task=tid, status='done', instructions='evidence.md', reviews=self.reviews(), evidence=[self.evidence(k) for k in task['required']])

    def test_seed_has_full_lifecycle_without_false_completion(self):
        s = w.read_json(self.path)
        w.validate(s, self.root)
        self.assertEqual(len(s['tasks']), 20)
        self.assertTrue(all(t['status'] == 'pending' for t in s['tasks']))
        self.assertEqual(s['tasks'][-1]['stage'], 'S9')

    def test_complete_entire_lifecycle_with_explicit_evidence(self):
        self.apply(op='add', item=dict(id='T52',stage='S5',title='制作批次',goal='完成约定内容',depends=['T41']))
        for tid in ['T00','T01','T10','T11','T20','T21','T30','T31','T40','T41','T51','T52','T50','T60','T61','T70','T71','T80','T81','T90','T91']:
            s = self.complete(tid)
        self.assertTrue(all(t['status'] == 'done' for t in s['tasks']))
        self.assertEqual(s['revision'], 22)

    def test_done_requires_reviews_and_evidence(self):
        with self.assertRaises(w.Invalid):
            self.apply(op='update', task='T00', status='done')
        self.assertEqual(w.read_json(self.path)['revision'], 0)

    def test_later_stage_cannot_skip_dependencies(self):
        with self.assertRaises(w.Invalid):
            self.complete('T10')

    def test_complete_backup_and_conflict(self):
        s = self.complete('T00')
        self.assertEqual(s['tasks'][0]['status'], 'done')
        self.assertTrue((self.root / 'backups/state-r0.json').is_file())
        with self.assertRaises(w.Invalid):
            self.apply(op='feedback', task='T00', kind='idea', text='late writer', revision=0)
        self.assertEqual(w.read_json(self.path)['revision'], 1)

    def test_missing_and_outside_evidence_rejected(self):
        for path in ('missing.md', '../outside.md'):
            e = self.evidence(); e['path'] = path
            with self.assertRaises(w.Invalid):
                self.apply(op='update', task='T00', evidence=[e])

    def test_latest_failure_overrides_success(self):
        self.apply(op='update', task='T00', instructions='evidence.md', evidence=[self.evidence(), self.evidence(result='fail')], reviews=self.reviews())
        with self.assertRaises(w.Invalid):
            self.apply(op='update', task='T00', status='done')

    def test_customer_evidence_required_after_scope_stage(self):
        self.complete('T00'); self.complete('T01')
        with self.assertRaises(w.Invalid):
            self.apply(op='update', task='T10', status='done', evidence=[self.evidence()], reviews=self.reviews())
        self.complete('T10')

    def test_feedback_is_not_acceptance(self):
        self.apply(op='feedback', task='T00', kind='experience', text='挺好的')
        self.assertEqual(w.read_json(self.path)['tasks'][0]['status'], 'pending')

    def test_problem_blocks_and_resolution_allows_acceptance(self):
        s = self.apply(op='feedback', task='T00', kind='problem', text='打不开')
        with self.assertRaises(w.Invalid):
            self.complete('T00')
        self.apply(op='resolve', feedback=s['feedback'][0]['id'], resolution='已定位并复测')
        self.complete('T00')

    def test_problem_after_acceptance_waits_for_triage(self):
        self.complete('T00'); self.complete('T01'); self.complete('T10')
        s = self.apply(op='feedback', task='T00', kind='problem', text='再次出现问题')
        self.assertEqual(s['tasks'][0]['status'], 'done')
        self.assertEqual(s['tasks'][2]['status'], 'done')
        self.assertTrue(w.view(s,self.root)['tasks'][0]['attention'])
        self.assertTrue(s['tasks'][0]['evidence'])

    def test_reopen_follows_same_stage_dependency(self):
        self.apply(op='update', task='T01', depends=['T00'])
        self.complete('T00'); self.complete('T01')
        s = self.apply(op='reopen', task='T00', reason='需求发生变化')
        self.assertEqual(s['tasks'][1]['status'], 'review')

    def test_minimum_checks_and_cycle_protected(self):
        with self.assertRaises(w.Invalid):
            self.apply(op='update', task='T10', required=['document'])
        with self.assertRaises(w.Invalid):
            self.apply(op='update', task='T00', depends=['T01'])

    def test_add_task_and_prevent_unnoticed_new_scope(self):
        s = self.apply(op='add', item=dict(id='T52', stage='S5', title='更多内容', goal='真实范围任务', depends=['T41']))
        self.assertEqual(len(s['tasks']), 21)
        self.complete('T00')
        s=self.apply(op='add', item=dict(id='T02', stage='S0', title='新范围', goal='检查点等待新任务'))
        self.assertEqual(s['tasks'][0]['status'],'done')
        self.assertIn('T02',w.dependencies(s,s['tasks'][1]))

    def test_lock_and_corruption_do_not_reset(self):
        (self.root / '.state.lock').write_text('test')
        with self.assertRaises(w.Invalid):
            self.apply(op='update', task='T00', status='active')
        (self.root / '.state.lock').unlink()
        self.path.write_text('{broken', encoding='utf-8')
        with self.assertRaises(ValueError):
            w.apply(dict(revision=0, op='update', task='T00', status='active'), self.path, self.root)
        self.assertEqual(self.path.read_text(), '{broken')

    def test_scope_change_requires_invalidating_evidence(self):
        self.apply(op='update', task='T00', instructions='evidence.md', evidence=[self.evidence()])
        with self.assertRaises(w.Invalid):
            self.apply(op='update', task='T00', goal='changed scope')

    def test_blocked_needs_explanation(self):
        with self.assertRaises(w.Invalid):
            self.apply(op='update', task='T00', status='blocked')

    def test_question_requires_explicit_resolution(self):
        self.apply(op='update', task='T00', question='选择哪一种体验？')
        self.apply(op='feedback', task='T00', kind='idea', text='选择第一种')
        with self.assertRaises(w.Invalid):
            self.complete('T00')
        self.apply(op='update', task='T00', question='')
        self.complete('T00')


    def test_missing_evidence_allows_read_feedback_and_repair(self):
        self.complete('T00')
        (self.root/'evidence.md').rename(self.root/'renamed.md')
        state=w.read_json(self.path); w.validate(state,self.root)
        self.assertTrue(w.view(state,self.root)['tasks'][0]['attention'])
        self.apply(op='feedback',task='T00',kind='problem',text='文件改名')
        s=self.apply(op='repair_reference',task='T00',old='evidence.md',new='renamed.md',reason='核对同一份原记录的移动')
        self.assertEqual(s['tasks'][0]['evidence'][0]['path'],'renamed.md')
        self.apply(op='resolve',feedback=s['feedback'][0]['id'],resolution='引用已修复')
        self.assertTrue(w.view(w.read_json(self.path),self.root)['tasks'][0]['accepted'])

    def test_optional_failure_requires_explicit_retest(self):
        self.apply(op='update',task='T00',instructions='evidence.md',evidence=[self.evidence('automated','fail')])
        with self.assertRaises(w.Invalid): self.complete('T00')
        e=self.evidence('automated');e['resolves']=[0]
        self.apply(op='update',task='T00',evidence=[e])
        self.complete('T00')

    def test_nonblocking_disposition_needs_customer_record(self):
        self.apply(op='update',task='T00',instructions='evidence.md',evidence=[self.evidence('automated','fail')])
        args=dict(op='dispose_failure',task='T00',index=0,decision='accepted_nonblocking',reason='isolated fixture',by='test',path='evidence.md')
        with self.assertRaises(w.Invalid): self.apply(**args)
        self.apply(**args,approval='evidence.md',approved_by='fixture customer')
        self.complete('T00')

    def test_required_failure_cannot_be_waived(self):
        self.apply(op='update',task='T00',instructions='evidence.md',evidence=[self.evidence(result='fail')],reviews=self.reviews())
        self.apply(op='dispose_failure',task='T00',index=0,decision='not_applicable',reason='test',by='test',path='evidence.md')
        with self.assertRaises(w.Invalid):self.apply(op='update',task='T00',status='done')

    def test_content_batch_cannot_depend_on_own_checkpoint(self):
        with self.assertRaises(w.Invalid):
            self.apply(op='add',item=dict(id='T52',stage='S5',title='批次',goal='内容',depends=['T50']))

    def test_lifecycle_release_expansion_and_patch(self):
        self.test_complete_entire_lifecycle_with_explicit_evidence()
        s=self.apply(op='snapshot_release',label='1.0',by='test',manifest='evidence.md',publication='local_delivery')
        snapshot=s['releases'][0]
        self.apply(op='add',item=dict(id='P101',stage='S9',title='维护补丁',goal='isolated fixture',depends=['T81'],required=['document','build','player']))
        self.complete('P101')
        s=self.apply(op='snapshot_release',label='1.0.1',by='test',manifest='evidence.md',publication='local_delivery',tasks=['P101'])
        self.assertEqual(s['releases'][0],snapshot)
        s=self.apply(op='add',item=dict(id='T53',stage='S5',title='新章节',goal='isolated fixture',depends=['T41']))
        tasks={t['id']:t for t in s['tasks']}
        self.assertEqual(tasks['T51']['status'],'done')
        self.assertNotEqual(tasks['T50']['status'],'done')
        self.assertEqual(s['releases'][0],snapshot)
        self.assertTrue(tasks['T81']['history'])

    def test_environment_reopen_does_not_reopen_game_design(self):
        self.complete('T00');self.complete('T01');self.complete('T10')
        s=self.apply(op='reopen',task='T00',reason='更换网页启动环境')
        self.assertEqual(s['tasks'][2]['status'],'done')


class HTTPTests(WorkflowTests):
    # Inherit fixtures, but run only HTTP-specific tests in this class.
    def setUp(self):
        super().setUp()
        self.original_root, self.original_state = server.ROOT, w.STATE
        server.ROOT, w.STATE = self.root, self.path
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:' + str(self.http.server_port)
        self.addCleanup(self.stop)

    def stop(self):
        self.http.shutdown(); self.http.server_close(); self.thread.join()
        server.ROOT, w.STATE = self.original_root, self.original_state

    def test_http_rejects_foreign_origin(self):
        req = Request(self.url + '/api/feedback', b'{}', headers={'Content-Type':'application/json','Origin':'https://example.com'})
        with self.assertRaises(HTTPError) as c:
            urlopen(req)
        self.assertEqual(c.exception.code, 403)

    def test_http_feedback_cannot_override_operation(self):
        data = dict(op='update', revision=0, task='T00', kind='idea', text='<script>not executed</script>', status='done')
        req = Request(self.url+'/api/feedback', json.dumps(data).encode(), headers={'Content-Type':'application/json','Origin':self.url})
        with urlopen(req) as r:
            s = json.load(r)
        self.assertEqual(s['tasks'][0]['status'], 'pending')
        self.assertEqual(s['feedback'][0]['text'], data['text'])
        self.assertEqual(w.read_json(self.path)['revision'], 1)

    def test_http_denies_arbitrary_files(self):
        for path in ('/docs/../state.json', '/.git/config', '/docs/state.json'):
            with self.assertRaises(HTTPError) as c:
                urlopen(self.url + path)
            self.assertEqual(c.exception.code, 404)

    def test_http_serves_only_registered_evidence(self):
        self.apply(op='update', task='T00', instructions='evidence.md', evidence=[self.evidence()])
        with urlopen(self.url + '/evidence/evidence.md') as r:
            self.assertIn(b'Isolated test fixture', r.read())
        (self.root / 'private.txt').write_text('not registered')
        with self.assertRaises(HTTPError) as c:
            urlopen(self.url + '/evidence/private.txt')
        self.assertEqual(c.exception.code, 404)

    def test_http_missing_evidence_keeps_read_export_feedback_available(self):
        self.complete('T00')
        (self.root / 'evidence.md').rename(self.root / 'moved.md')
        with urlopen(self.url + '/api/state') as r:
            self.assertTrue(json.load(r)['tasks'][0]['attention'])
        with urlopen(self.url + '/api/export') as r:
            self.assertEqual(json.load(r)['revision'], 1)
        req = Request(self.url + '/api/feedback', json.dumps(dict(revision=1,task='T00',kind='problem',text='文件移动')).encode(), headers={'Content-Type':'application/json','Origin':self.url})
        with urlopen(req) as r:
            self.assertEqual(json.load(r)['feedback'][0]['text'], '文件移动')


# Do not duplicate the state tests under HTTPTests.
for name in list(WorkflowTests.__dict__):
    if name.startswith('test_'):
        setattr(HTTPTests, name, None)


if __name__ == '__main__':
    unittest.main()
