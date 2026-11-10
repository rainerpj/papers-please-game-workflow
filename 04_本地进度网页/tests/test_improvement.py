"""Isolated protocol tests; fixture evidence is not real customer validation."""
import copy
from datetime import datetime, timezone, timedelta
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import improvement as m
import workflow as w


class ImprovementTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / '项目记录').mkdir()
        (self.root / 'docs').mkdir()
        self.rule = self.root / 'docs/rule.md'
        self.rule.write_text('before', encoding='utf-8')
        (self.root / 'case.md').write_text('Isolated fixture, not customer evidence.', encoding='utf-8')
        w.atomic(self.root / '项目记录/state.json', w.seed())

    def apply(self, **payload):
        payload.setdefault('revision', m.load(self.root)['revision'])
        return m.apply(payload, self.root)

    def signal(self, key='chat:1'):
        return dict(key=key, locator='本项目对话/turn-1', text='已经写过设定', text_kind='quote', at='2026-11-10T00:00:00+00:00')

    def ingest(self, key='chat:1', cursor='1'):
        before = m.load(self.root)['sources'].get('chat', {}).get('cursor', '')
        return self.apply(op='ingest', stream='chat', from_cursor=before, cursor=cursor, coverage='complete', note='isolated source', entries=[self.signal(key)])

    def verified(self, classification='workflow'):
        self.ingest()
        self.apply(op='record', key='read-first', title='先读资料再问', signals=['chat:1'])
        return self.apply(op='assess', item='I1', classification=classification, cause='漏读资料', expected='先读并标来源', case_path='case.md')

    def sealed(self):
        self.verified()
        self.apply(op='prepare', item='I1', files=['docs/rule.md'])
        self.rule.write_text('after', encoding='utf-8')
        return self.apply(op='seal', item='I1')

    def record(self):
        return dict(path='case.md', result='pass', note='isolated test', by='test', at=w.now())

    def trial(self):
        self.sealed()
        return self.apply(op='trial', item='I1', checks={k:self.record() for k in ('correctness','necessity','original','related')}, validation_mode='scenario_review')

    def test_missing_ledger_is_compatible_and_not_written_on_read(self):
        state=m.load(self.root)
        self.assertEqual(state['items'], [])
        self.assertFalse(m.path_for(self.root).exists())

    def test_ingest_deduplicates_and_records_cursor(self):
        self.ingest(); state=self.ingest()
        self.assertEqual(len(state['signals']), 1)
        self.assertEqual(state['sources']['chat']['cursor'], '1')

    def test_source_conflict_does_not_advance_cursor(self):
        self.ingest(); original=m.load(self.root)
        changed=self.signal(); changed['text']='different'
        with self.assertRaises(w.Invalid):
            self.apply(op='ingest',stream='chat',from_cursor='1',cursor='2',coverage='complete',note='test',entries=[changed])
        self.assertEqual(m.load(self.root), original)

    def test_unavailable_history_cannot_claim_cursor_progress(self):
        with self.assertRaises(w.Invalid):
            self.apply(op='ingest',stream='chat',cursor='100',coverage='unavailable',note='读不到历史',entries=[])
        s=self.apply(op='ingest',stream='chat',coverage='unavailable',note='读不到历史，只用项目记录',entries=[])
        self.assertEqual(s['sources']['chat']['cursor'],'')

    def test_stale_revision_and_cursor_rejected(self):
        self.ingest()
        with self.assertRaises(w.Invalid):self.apply(op='policy',revision=0,tasks=3,days=7)
        with self.assertRaises(w.Invalid):self.apply(op='ingest',stream='chat',from_cursor='',cursor='2',coverage='complete',note='test',entries=[])

    def test_feedback_sync_references_original_and_is_idempotent(self):
        path=self.root/'项目记录/state.json'
        gs=w.apply(dict(op='feedback',revision=0,scope='workflow',kind='problem',text='说明看不懂'),path,self.root)
        s=self.apply(op='sync_feedback'); again=self.apply(op='sync_feedback')
        self.assertEqual(s,again)
        self.assertNotIn('说明看不懂',s['signals'][0]['text'])
        self.assertEqual(gs,w.read_json(path))

    def test_record_requires_sources_and_merges_duplicates(self):
        with self.assertRaises(w.Invalid):self.apply(op='record',key='x',title='x',signals=['unknown'])
        self.verified(); s=self.apply(op='record',key='read-first',title='same',signals=['chat:1'])
        self.assertEqual(len(s['items']),1)

    def test_game_preference_is_not_permanent_workflow_rule(self):
        self.verified('game_requirement')
        with self.assertRaises(w.Invalid):self.apply(op='prepare',item='I1',files=['docs/rule.md'])
        s=self.apply(op='decide',item='I1',status='dismissed',reason='转游戏需求，不永久禁止某种美术风格')
        self.assertEqual(s['items'][0]['status'],'dismissed')

    def test_prepare_protects_game_state_core_and_external_files(self):
        self.verified()
        for ref in ('项目记录/state.json','Game/Assets/code.cs','AGENTS.md','04_本地进度网页/WORKFLOW.PY','../other.md','docs/../../other.md'):
            with self.subTest(ref=ref), self.assertRaises(w.Invalid):
                self.apply(op='prepare',item='I1',files=[ref])

    def test_cannot_skip_validation_or_claim_satisfaction(self):
        self.sealed()
        with self.assertRaises(w.Invalid):self.apply(op='trial',item='I1',checks={},validation_mode='automated')
        with self.assertRaises(w.Invalid):self.apply(op='observe',item='I1',outcome='effective',observation=self.record())

    def test_failed_check_prevents_trial(self):
        self.sealed()
        checks={k:self.record() for k in ('correctness','necessity','original','related')}
        checks['related']['result']='fail'
        with self.assertRaises(w.Invalid):self.apply(op='trial',item='I1',checks=checks,validation_mode='scenario_review')

    def test_trial_requires_post_trial_usage_evidence(self):
        self.trial(); obs=self.record()
        with self.assertRaises(w.Invalid):self.apply(op='observe',item='I1',outcome='effective',observation=obs)
        obs.update(kind='usage',at='2000-01-01T00:00:00+00:00')
        with self.assertRaises(w.Invalid):self.apply(op='observe',item='I1',outcome='effective',observation=obs)
        obs['at']=w.now()
        s=self.apply(op='observe',item='I1',outcome='effective',observation=obs)
        self.assertEqual(s['items'][0]['status'],'effective')

    def test_missing_evidence_is_visible_but_not_effective(self):
        self.trial(); (self.root/'case.md').unlink()
        self.assertTrue(m.view(m.load(self.root),m.game(self.root),self.root)['items'][0]['missing_evidence'])
        with self.assertRaises(w.Invalid):self.apply(op='observe',item='I1',outcome='effective',observation=self.record()|{'kind':'usage'})

    def test_recurrence_preserves_old_result_and_creates_new_candidate(self):
        self.trial()
        self.apply(op='observe',item='I1',outcome='effective',observation=self.record()|{'kind':'usage'})
        self.ingest('chat:2','2')
        s=self.apply(op='record',key='read-first',title='仍有重复问',signals=['chat:2'])
        self.assertEqual([i['status'] for i in s['items']],['effective','candidate'])
        self.assertEqual(s['items'][1]['previous'],'I1')

    def test_rollback_restores_only_own_files_not_game_progress(self):
        self.trial(); gs=m.game(self.root)
        s=self.apply(op='rollback',item='I1',reason='反效果')
        self.assertEqual(self.rule.read_text(),'before')
        self.assertEqual(s['items'][0]['status'],'reverted')
        self.assertEqual(m.game(self.root),gs)

    def test_rollback_refuses_later_edits_without_overwriting(self):
        self.trial(); self.rule.write_text('customer change')
        with self.assertRaises(w.Invalid):self.apply(op='rollback',item='I1',reason='test')
        self.assertEqual(self.rule.read_text(),'customer change')
        self.assertEqual(m.load(self.root)['items'][0]['status'],'trial')

    def test_interrupted_rollback_can_resume(self):
        self.sealed()
        original=m.os.replace
        def fail_rule(source,target):
            if Path(target)==self.rule:raise OSError('simulated interruption')
            return original(source,target)
        with patch.object(m.os,'replace',side_effect=fail_rule):
            with self.assertRaises(OSError):self.apply(op='rollback',item='I1',reason='test')
        self.assertEqual(m.load(self.root)['items'][0]['status'],'reverting')
        self.apply(op='rollback',item='I1',reason='恢复中断的回退')
        self.assertEqual(self.rule.read_text(),'before')

    def test_rollback_removes_only_new_file_from_this_change(self):
        self.verified(); self.apply(op='prepare',item='I1',files=['docs/new.md'])
        added=self.root/'docs/new.md';added.write_text('new')
        self.apply(op='seal',item='I1');self.apply(op='rollback',item='I1',reason='test')
        self.assertFalse(added.exists());self.assertTrue(self.rule.exists())

    def test_changed_after_seal_requires_new_validation(self):
        self.sealed();self.rule.write_text('third version')
        with self.assertRaises(w.Invalid):self.apply(op='trial',item='I1',checks={k:self.record() for k in ('correctness','necessity','original','related')},validation_mode='scenario_review')

    def test_due_counts_completions_stages_and_days_with_new_material(self):
        s=m.seed();gs=w.seed();at=datetime.now(timezone.utc)
        s['cycle']['at']=(at-timedelta(days=8)).isoformat()
        self.assertEqual(m.due(s,gs,at)['cycle_reasons'],[])
        gs['revision']=5;gs['events']=[dict(revision=n,completed=True,task='X'+str(n)) for n in range(1,6)]
        self.assertEqual(len(m.due(s,gs,at)['cycle_reasons']),2)
        gs['events'][0]['task']='T01'
        self.assertEqual(len(m.due(s,gs,at)['cycle_reasons']),3)

    def test_task_review_does_not_reset_cycle_counter(self):
        s=m.load(self.root);original=copy.deepcopy(s['cycle'])
        self.apply(op='review',scope='task',game_revision=0,note='没有新问题')
        self.assertEqual(m.load(self.root)['cycle'],original)
        with self.assertRaises(w.Invalid):self.apply(op='review',scope='cycle',game_revision=10,note='stale')

    def test_indexing_feedback_is_not_assessment(self):
        path=self.root/'项目记录/state.json'
        gs=w.apply(dict(op='feedback',revision=0,scope='workflow',kind='problem',text='看不懂'),path,self.root)
        self.apply(op='sync_feedback')
        self.assertEqual(m.due(m.load(self.root),gs)['unprocessed_feedback'],['F1'])
        self.apply(op='record',key='clarity',title='看不懂',signals=['feedback:F1'])
        self.assertEqual(m.due(m.load(self.root),gs)['unprocessed_feedback'],['F1'])
        self.apply(op='assess',item='I1',classification='workflow',cause='说明不清',expected='可执行',case_path='case.md')
        self.assertEqual(m.due(m.load(self.root),gs)['unprocessed_feedback'],[])

    def test_rollback_checks_all_targets_before_first_write(self):
        self.verified()
        other=self.root/'docs/other.md';other.write_text('original')
        self.apply(op='prepare',item='I1',files=['docs/rule.md','docs/other.md'])
        self.rule.write_text('changed');other.write_text('changed')
        self.apply(op='seal',item='I1');other.write_text('customer edits')
        with self.assertRaises(w.Invalid):self.apply(op='rollback',item='I1',reason='test')
        self.assertEqual(self.rule.read_text(),'changed')
        self.assertEqual(other.read_text(),'customer edits')

    def test_new_evidence_reopens_assessment(self):
        self.verified();self.ingest('chat:2','2')
        result=self.apply(op='record',key='read-first',title='新信息',signals=['chat:2'])
        self.assertEqual(result['items'][0]['status'],'candidate')
        with self.assertRaises(w.Invalid):self.apply(op='prepare',item='I1',files=['docs/rule.md'])

    def test_unused_preparation_can_be_cancelled(self):
        self.verified();self.apply(op='prepare',item='I1',files=['docs/rule.md'])
        state=self.apply(op='cancel',item='I1',reason='无需修改')
        self.assertEqual(state['items'][0]['status'],'dismissed')
        self.assertEqual(self.rule.read_text(),'before')

    def test_cancellation_cannot_hide_changed_files(self):
        self.verified();self.apply(op='prepare',item='I1',files=['docs/rule.md'])
        self.rule.write_text('actual edit')
        with self.assertRaises(w.Invalid):self.apply(op='cancel',item='I1',reason='test')
        self.apply(op='seal',item='I1');self.apply(op='rollback',item='I1',reason='test')
        self.assertEqual(self.rule.read_text(),'before')

    def test_corrupt_ledger_never_resets(self):
        m.path_for(self.root).write_text('{broken')
        with self.assertRaises(ValueError):m.load(self.root)
        self.assertEqual(m.path_for(self.root).read_text(),'{broken')


if __name__ == '__main__':
    unittest.main()
