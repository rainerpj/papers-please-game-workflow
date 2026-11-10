"""Local workflow state. Standard library only; no model or cloud connection."""
import argparse
import copy
import json
import os
from pathlib import Path
import tempfile
from datetime import datetime, timezone
from contextlib import contextmanager

ROOT = Path(__file__).resolve().parent.parent
CONFIG = Path(__file__).with_name('workflow.json')
STATE = ROOT / '项目记录' / 'state.json'


class Invalid(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def config():
    return read_json(CONFIG)


def seed():
    tasks = []
    for stage in config()['stages']:
        for tid, title, goal in stage['tasks']:
            tasks.append(dict(id=tid, stage=stage['id'], title=title, goal=goal,
                              status='pending', evidence=[], reviews={}, blocker='', question='',
                              required=stage['checks'], depends=[], history=[]))
    return dict(schema=1, revision=0, title='我的证件审核游戏',
                direction='《Papers, Please》式审核与判断游戏；完整游戏全生命周期',
                updated=now(), tasks=tasks, feedback=[], events=[])


def reference_path(root, ref):
    if not isinstance(ref, str) or not ref.strip():
        raise Invalid('证据必须填写项目内文件路径。')
    path = (root / ref).resolve()
    if not path.is_relative_to(root.resolve()):
        raise Invalid('文件位于项目外：' + ref)
    if any(part.startswith('.') for part in Path(ref).parts):
        raise Invalid('请将证据放在可交付的项目记录目录内。')
    return path


def safe_file(root, ref):
    path = reference_path(root, ref)
    if not path.is_file():
        raise Invalid('文件不存在：' + ref)
    return path


def dependencies(state, task):
    cfg = config()
    deps = set(task['depends']) | set(cfg.get('dependencies', {}).get(task['id'], []))
    if cfg.get('checkpoints', {}).get(task['stage']) == task['id']:
        deps.update(t['id'] for t in state['tasks'] if t['stage'] == task['stage'] and t['id'] != task['id'])
    return sorted(deps)


def nonempty(value, label, maximum=4000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise Invalid(label + '不能为空或过长。')


def validate(state, root=ROOT):
    cfg = config()
    stages = {s['id']: s for s in cfg['stages']}
    if state.get('schema') != 1 or type(state.get('revision')) is not int:
        raise Invalid('不支持的进度格式。请保留原文件并让 AI 检查。')
    tasks = state.get('tasks')
    if not isinstance(tasks, list) or not tasks:
        raise Invalid('任务列表不可为空。')
    ids = [t['id'] for t in tasks]
    if len(set(ids)) != len(ids):
        raise Invalid('任务编号重复。')
    baseline = {t[0] for s in cfg['stages'] for t in s['tasks']}
    if not baseline.issubset(ids):
        raise Invalid('生命周期基线任务不可删除；应调整其范围并记录原因。')
    for task in tasks:
        if task['stage'] not in stages or task['status'] not in cfg['status_labels']:
            raise Invalid('无效阶段或状态。')
        nonempty(task['title'], '任务名称', 150)
        nonempty(task['goal'], '任务目标')
        if not isinstance(task.get('question', ''), str) or len(task.get('question', '')) > 2000:
            raise Invalid('待回答问题必须为不超过 2000 字的文本。')
        required = task['required']
        minimum = stages[task['stage']]['checks'] if task['id'] in baseline else ['document']
        if not isinstance(required, list) or not set(minimum).issubset(required) or not set(required).issubset(cfg['check_labels']):
            raise Invalid('不能减少阶段最低验收项。')
        if not isinstance(task['depends'], list) or any(d not in ids or d == task['id'] for d in task['depends']):
            raise Invalid('任务依赖无效。')
        for e in task['evidence']:
            if e['kind'] not in cfg['check_labels'] or e['result'] not in ('pass', 'fail'):
                raise Invalid('证据类别或结果无效。')
            nonempty(e['note'], '证据说明')
            nonempty(e['by'], '执行人', 100)
            nonempty(e['at'], '执行时间', 100)
            reference_path(root, e['path'])
        for r in task['reviews'].values():
            reference_path(root, r['path'])
        for index, e in enumerate(task['evidence']):
            for resolved in e.get('resolves', []):
                if e['result'] != 'pass' or type(resolved) is not int or not 0 <= resolved < index or task['evidence'][resolved]['result'] != 'fail':
                    raise Invalid('复测关联无效。')
        for d in task.get('dispositions', []):
            index = d.get('index')
            if type(index) is not int or not 0 <= index < len(task['evidence']) or task['evidence'][index]['result'] != 'fail' or d.get('decision') not in ('not_applicable', 'accepted_nonblocking'):
                raise Invalid('失败处置无效。')
            for key in ('reason', 'by', 'at'):
                nonempty(d.get(key), '处置' + key)
            reference_path(root, d.get('path'))
            if d['decision'] == 'accepted_nonblocking':
                reference_path(root, d.get('approval'))
                nonempty(d.get('approved_by'), '客户确认人')
        if task.get('instructions'):
            reference_path(root, task['instructions'])
        if task['status'] == 'blocked':
            nonempty(task['blocker'], '阻碍说明')
    # Dependencies must not point into a later stage or form a cycle.
    by_id = {t['id']: t for t in tasks}
    order = list(stages)
    visiting, visited = set(), set()
    def visit(tid):
        if tid in visiting:
            raise Invalid('任务依赖形成循环。')
        if tid in visited:
            return
        visiting.add(tid)
        for dep in dependencies(state, by_id[tid]):
            if order.index(by_id[dep]['stage']) > order.index(by_id[tid]['stage']):
                raise Invalid('不能依赖后续阶段。')
            visit(dep)
        visiting.remove(tid)
        visited.add(tid)
    for tid in ids:
        visit(tid)


def blockers(state, task, root=ROOT, admission=False, memo=None):
    memo = {} if memo is None else memo
    if task['id'] in memo:
        return memo[task['id']]
    cfg = config()
    order = [s['id'] for s in cfg['stages']]
    earlier = [t for t in state['tasks'] if t['id'] in dependencies(state, task) or (admission and order.index(t['stage']) < order.index(task['stage']))]
    result = []
    if any(t['status'] != 'done' or blockers(state, t, root, memo=memo) for t in earlier):
        result.append('先完成前置阶段或依赖任务')
    try:
        safe_file(root, task.get('instructions', ''))
    except Invalid:
        result.append('需要本步操作卡：AI 要做什么、客户怎么做、怎样测试和卡住后怎么办')
    for name, label in [('correctness', '正确性审查'), ('necessity', '必要性审查')]:
        r = task['reviews'].get(name, {})
        if r.get('result') != 'pass' or not r.get('note') or not r.get('path'):
            result.append('缺少通过的' + label)
        else:
            try:
                safe_file(root, r['path'])
            except Invalid:
                result.append(label + '证据文件无效')
    # Latest recorded result for a category wins: later failure invalidates earlier success.
    latest = {e['kind']: e for e in task['evidence']}
    for kind in task['required']:
        if latest.get(kind, {}).get('result') != 'pass':
            result.append('缺少通过的' + cfg['check_labels'][kind])
        else:
            try:
                safe_file(root, latest[kind]['path'])
            except Invalid:
                result.append(cfg['check_labels'][kind] + '证据文件失效')
    resolved = {i for e in task['evidence'] if e['result'] == 'pass' and reference_path(root, e['path']).is_file() for i in e.get('resolves', [])}
    resolved.update(d['index'] for d in task.get('dispositions', []) if reference_path(root, d['path']).is_file() and (d['decision'] == 'not_applicable' or reference_path(root, d['approval']).is_file()))
    for index, e in enumerate(task['evidence']):
        if e['result'] == 'fail' and index not in resolved:
            result.append('失败证据 E' + str(index) + ' 尚未明确复测解决')
    if task['id'] == 'T50' and not any(t['stage'] == 'S5' and t['id'] not in ('T50', 'T51') for t in state['tasks']):
        result.append('先按内容矩阵建立并完成实际制作批次')
    if task['blocker'].strip():
        result.append('仍有阻碍：' + task['blocker'])
    if task.get('question', '').strip():
        result.append('等待客户决定：' + task['question'])
    if any(f.get('scope', 'task') != 'workflow' and f['task'] == task['id'] and f['status'] == 'open' and f['kind'] == 'problem' for f in state['feedback']):
        result.append('仍有未处理的问题反馈')
    memo[task['id']] = result
    return result


def view(state, root=ROOT):
    result = copy.deepcopy(state)
    for task in result['tasks']:
        task['gaps'] = blockers(state, task, root)
        task['effective_depends'] = dependencies(state, task)
        task['accepted'] = task['status'] == 'done' and not task['gaps']
        task['attention'] = task['status'] == 'done' and bool(task['gaps'])
    return result


@contextmanager
def locked(path):
    lock = path.parent / '.state.lock'
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise Invalid('另一个操作正在保存。请稍后重试；若持续出现，请让 AI 检查锁文件。')
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        lock.unlink(missing_ok=True)


def atomic(path, state):
    fd, temp = tempfile.mkstemp(prefix='state-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(state, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def reopen(state, task, reason):
    tasks = {t['id']: t for t in state['tasks']}
    affected = {task['id']}
    while True:
        more = {t['id'] for t in state['tasks'] if set(dependencies(state, t)) & affected}
        if more.issubset(affected):
            break
        affected |= more
    for tid in affected:
        t = tasks[tid]
        if tid != task['id'] and t['status'] == 'pending' and not t['evidence'] and not t['reviews']:
            continue
        t['history'].append(dict(at=now(), reason=reason, status=t['status'], evidence=t['evidence'], reviews=t['reviews'], dispositions=t.get('dispositions', [])))
        t.update(status='pending' if tid == task['id'] else 'review', evidence=[], reviews={}, dispositions=[], blocker='', question='', review_reason=reason)


def apply(payload, path=STATE, root=ROOT):
    with locked(path):
        state = read_json(path)
        validate(state, root)
        if type(payload.get('revision')) is not int or payload['revision'] != state['revision']:
            raise Invalid('进度已变化，请刷新后重新操作。原记录未被覆盖。')
        old = copy.deepcopy(state)
        op = payload.get('op')
        tasks = {t['id']: t for t in state['tasks']}
        task = tasks.get(payload.get('task'))
        if (op in ('update', 'reopen') or (op == 'feedback' and payload.get('scope', 'task') != 'workflow')) and task is None:
            raise Invalid('找不到这项任务。')
        if op == 'update':
            if task['status'] == 'done':
                raise Invalid('已验收任务需先 reopen，再修改并重新验证。')
            if any(k in payload and payload[k] != task.get(k) for k in ('title', 'goal', 'depends', 'instructions')) and (task['evidence'] or task['reviews']):
                raise Invalid('已有证据的任务变更目标或依赖前，请先 reopen 使旧证据归档。')
            for field in ('title', 'goal', 'status', 'blocker', 'question', 'required', 'depends', 'instructions', 'version'):
                if field in payload:
                    task[field] = payload[field]
            if 'evidence' in payload:
                for e in payload['evidence']:
                    safe_file(root, e.get('path'))
                    for i in e.get('resolves', []):
                        if e.get('result') != 'pass' or type(i) is not int or not 0 <= i < len(task['evidence']) or task['evidence'][i]['result'] != 'fail':
                            raise Invalid('resolves 必须指向本任务已有失败证据的零起始索引。')
                task['evidence'].extend(payload['evidence'])
            if payload.get('instructions'):
                safe_file(root, payload['instructions'])
            if task['status'] == 'active':
                safe_file(root, task.get('instructions', ''))
            if 'reviews' in payload:
                for key, review in payload['reviews'].items():
                    if key not in ('correctness', 'necessity') or review.get('result') not in ('pass', 'fail'):
                        raise Invalid('审查记录无效。')
                    for field in ('note', 'by', 'at'):
                        nonempty(review.get(field), '审查' + field)
                    safe_file(root, review.get('path'))
                task['reviews'].update(payload['reviews'])
        elif op == 'reopen':
            nonempty(payload.get('reason'), '重新打开原因')
            reopen(state, task, payload['reason'])
        elif op == 'add':
            t = payload.get('item', {})
            stage = next((s for s in config()['stages'] if s['id'] == t.get('stage')), None)
            if not stage or t.get('id') in tasks or not isinstance(t.get('id'), str) or not t['id'].isascii() or not t['id'].replace('-', '').isalnum():
                raise Invalid('新任务编号或阶段无效。')
            state['tasks'].append(dict(id=t['id'], stage=t['stage'], title=t['title'], goal=t['goal'], depends=t.get('depends', []), required=t.get('required', ['document']), instructions=t.get('instructions', ''), version=t.get('version', ''), status='pending', blocker='', question='', evidence=[], reviews={}, history=[]))
            validate(state, root)
            checkpoint = config().get('checkpoints', {}).get(t['stage'])
            if checkpoint:
                reopen(state, tasks[checkpoint], '新增制作任务 ' + t['id'] + '，阶段结论需要重新核对')
        elif op == 'feedback':
            nonempty(payload.get('text'), '反馈内容', 3000)
            if payload.get('kind') not in ('idea', 'problem', 'experience'):
                raise Invalid('反馈类型无效。')
            scope = payload.get('scope', 'task')
            if scope not in ('task', 'workflow'):
                raise Invalid('反馈关联范围无效。')
            state['feedback'].append(dict(id='F' + str(state['revision'] + 1), task=task['id'] if task else '', scope=scope, text=payload['text'], kind=payload['kind'], status='open', at=now(), resolution=''))
        elif op == 'route_feedback':
            f = next((f for f in state['feedback'] if f['id'] == payload.get('feedback')), None)
            nonempty(payload.get('reason'), '分类原因')
            if not f or f['status'] != 'open' or payload.get('scope') not in ('task', 'workflow'):
                raise Invalid('仅能对未处理反馈选择有效关联范围。')
            if payload['scope'] == 'task' and payload.get('task') not in tasks:
                raise Invalid('关联游戏任务不存在。')
            f.setdefault('routing', []).append(dict(at=now(), previous=f.get('scope', 'task'), reason=payload['reason']))
            f.update(scope=payload['scope'], task=payload.get('task', f['task']))
        elif op == 'dispose_failure':
            if task is None or task['status'] == 'done':
                raise Invalid('先重新打开需处置失败的任务。')
            index = payload.get('index')
            if type(index) is not int or not 0 <= index < len(task['evidence']) or task['evidence'][index]['result'] != 'fail':
                raise Invalid('失败证据索引无效。')
            if payload.get('decision') not in ('not_applicable', 'accepted_nonblocking'):
                raise Invalid('处置类型无效。')
            for key in ('reason', 'by'):
                nonempty(payload.get(key), key)
            safe_file(root, payload.get('path'))
            if payload['decision'] == 'accepted_nonblocking':
                safe_file(root, payload.get('approval'))
                nonempty(payload.get('approved_by'), '客户确认人')
            task.setdefault('dispositions', []).append({k: payload[k] for k in ('index', 'decision', 'reason', 'by', 'path', 'approval', 'approved_by') if k in payload} | {'at': now()})
        elif op == 'repair_reference':
            if task is None:
                raise Invalid('找不到任务。')
            nonempty(payload.get('reason'), '修复原因')
            safe_file(root, payload.get('new'))
            changed = False
            for item in task['evidence'] + list(task['reviews'].values()) + task.get('dispositions', []):
                if item['path'] == payload.get('old'):
                    item['path'] = payload['new']; changed = True
                if item.get('approval') == payload.get('old'):
                    item['approval'] = payload['new']; changed = True
            if task.get('instructions') == payload.get('old'):
                task['instructions'] = payload['new']; changed = True
            if not changed:
                raise Invalid('当前任务没有此引用。')
            task['history'].append(dict(at=now(), reason=payload['reason'], old=payload['old'], new=payload['new']))
        elif op == 'snapshot_release':
            nonempty(payload.get('label'), '版本号', 100)
            nonempty(payload.get('by'), '登记人', 100)
            safe_file(root, payload.get('manifest'))
            if payload.get('publication') not in ('local_delivery', 'published'):
                raise Invalid('说明是本地交付还是已发布。')
            releases = state.setdefault('releases', [])
            if any(r['label'] == payload['label'] for r in releases):
                raise Invalid('版本号已归档，不可覆盖。')
            selected = [t for t in state['tasks'] if t['stage'] != 'S9' or t['id'] in payload.get('tasks', [])]
            if any(t['status'] != 'done' or blockers(state, t, root) for t in selected):
                raise Invalid('交付任务或指定补丁任务尚未验收。')
            if any(i not in tasks for i in payload.get('tasks', [])):
                raise Invalid('补丁任务不存在。')
            releases.append(dict(label=payload['label'], at=now(), by=payload['by'], manifest=payload['manifest'], publication=payload['publication'], tasks=copy.deepcopy(selected), revision=state['revision']))
        elif op == 'resolve':
            nonempty(payload.get('resolution'), '处理说明')
            feedback = next((f for f in state['feedback'] if f['id'] == payload.get('feedback')), None)
            if not feedback or feedback['status'] != 'open':
                raise Invalid('反馈不存在或已处理。')
            feedback.update(status='resolved', resolution=payload['resolution'])
        else:
            raise Invalid('不支持的操作。')
        state['revision'] += 1
        state['updated'] = now()
        state['events'].append(dict(at=state['updated'], op=op, task=payload.get('task', ''), revision=state['revision'], reason=payload.get('reason', ''), completed=op == 'update' and task['status'] == 'done'))
        validate(state, root)
        if op == 'update' and task['status'] == 'done':
            issues = blockers(state, task, root, admission=True)
            if issues:
                raise Invalid(task['id'] + ' 尚不能验收：' + '；'.join(issues))
        backup = path.parent / 'backups'
        backup.mkdir(exist_ok=True)
        backup_path = backup / ('state-r' + str(old['revision']) + '.json')
        if backup_path.exists():
            backup_path = backup / ('state-r' + str(old['revision']) + '-' + datetime.now().strftime('%Y%m%d%H%M%S%f') + '.json')
        atomic(backup_path, old)
        atomic(path, state)
        return state


def main():
    parser = argparse.ArgumentParser(description='维护本地游戏项目进度')
    parser.add_argument('command', choices=['show', 'validate', 'apply'])
    parser.add_argument('--payload', type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'apply':
            if args.payload is None:
                raise Invalid('apply 需要 --payload 文件路径。')
            state = apply(read_json(args.payload))
        else:
            state = read_json(STATE)
            validate(state)
        print(json.dumps(view(state) if args.command == 'show' else {'ok': True, 'revision': state['revision']}, ensure_ascii=False, indent=2))
    except (Invalid, KeyError, TypeError, OSError, ValueError) as exc:
        parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    main()
