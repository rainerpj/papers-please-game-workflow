"""Review-only probes. All mutations occur in new, isolated temporary folders.

Synthetic evidence does not prove any Unity game works. Production files are imported,
not modified. Each assertion describes the observed V0.1 behavior, not desired behavior.
"""
from pathlib import Path
import json
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / '04_本地进度网页'))
import workflow as w

BASE = Path(tempfile.mkdtemp(prefix='novice-workflow-review-')).resolve()
results = []


class Fixture:
    def __init__(self, name):
        self.root = BASE / name
        self.root.mkdir()
        self.state = self.root / 'state.json'
        (self.root / 'evidence.md').write_text('SYNTHETIC review fixture, not game evidence.', encoding='utf-8')
        w.atomic(self.state, w.seed())

    def apply(self, **payload):
        payload['revision'] = w.read_json(self.state)['revision']
        return w.apply(payload, self.state, self.root)

    def evidence(self, kind, result='pass'):
        return dict(kind=kind, result=result, path='evidence.md', note='synthetic fixture', by='review probe', at=w.now())

    def complete(self, tid, extra=None):
        task = next(t for t in w.read_json(self.state)['tasks'] if t['id'] == tid)
        reviews = {k:dict(result='pass', path='evidence.md', note='synthetic review', by='review probe', at=w.now()) for k in ('correctness','necessity')}
        return self.apply(op='update', task=tid, status='done', reviews=reviews,
                          evidence=[self.evidence(k) for k in task['required']] + (extra or []))

    def complete_until(self, last):
        for task in w.seed()['tasks']:
            state = self.complete(task['id'])
            if task['id'] == last:
                return state


def rejected(action):
    try:
        action()
    except w.Invalid as exc:
        return str(exc)
    raise AssertionError('Expected current version to reject this operation')


f = Fixture('incremental-content')
f.complete_until('T51')
f.apply(op='reopen', task='T50', reason='scope update affecting only the content batch')
error = rejected(lambda:f.apply(op='add', item=dict(id='T52', stage='S5', title='第二批事件', goal='新增已确认事件')))
results.append(dict(id='R01', finding='重开受影响任务 T50 后，因同阶段 T51 已验收，仍不能新增 T52', error=error))

f = Fixture('missing-evidence')
f.complete('T00')
(f.root / 'evidence.md').rename(f.root / 'renamed-evidence.md')
errors = []
for payload in [dict(op='reopen', task='T00', reason='repair moved evidence'),
                dict(op='feedback', task='T00', kind='problem', text='记录打不开'),
                dict(op='update', task='T01', status='active')]:
    errors.append(rejected(lambda payload=payload:f.apply(**payload)))
results.append(dict(id='R02', finding='证据改名后，重开、报错和其他任务更新都被全量预校验阻止', errors=errors))

f = Fixture('released-project-support')
f.complete_until('T91')
state = f.apply(op='feedback', task='T00', kind='problem', text='进度网页暂时打不开，游戏本身正常')
reopened = [t['id'] for t in state['tasks'] if t['status'] == 'pending']
assert len(reopened) == 19
assert next(t for t in state['tasks'] if t['id']=='T81')['status']=='pending'
results.append(dict(id='R03', finding='已完成周期的准备阶段问题反馈使 19/20 项退回未开始，包括已交付游戏和维护', reopened=reopened))

f = Fixture('optional-failure')
f.complete_until('T30')
state = f.complete('T31', extra=[f.evidence('automated','fail')])
task = next(t for t in state['tasks'] if t['id']=='T31')
assert task['status']=='done' and any(e['result']=='fail' for e in task['evidence'])
results.append(dict(id='R04', finding='核心玩法已登记自动测试失败且未作处置，仍可被验收', status=task['status'], required=task['required']))

f = Fixture('production-subtask-order')
f.complete_until('T41')
f.apply(op='add', item=dict(id='T52', stage='S5', title='第二批事件', goal='完整版本必需的事件内容', depends=['T50']))
error = rejected(lambda:f.complete('T52'))
state = f.complete('T50')
assert next(t for t in state['tasks'] if t['id']=='T52')['status']=='pending'
results.append(dict(id='R05', finding='文档示例让第二批事件依赖完成全部内容；第二批不能先验收，但全部内容可先验收', subtask_error=error, aggregate_status='done', subtask_status='pending'))

f = Fixture('same-stage-order')
f.complete_until('T21')
state = f.complete('T31')
assert next(t for t in state['tasks'] if t['id']=='T30')['status']=='pending'
results.append(dict(id='R06', finding='基线没有同阶段依赖，玩法运行任务可在准备 Unity 工程任务未验收时通过；需区分合理并行和真实依赖', environment_status='pending', prototype_status='done'))

output = dict(scope='isolated state-machine probes; no actual game execution', fixture_root=str(BASE), results=results)
print(json.dumps(output, ensure_ascii=False, indent=2))
