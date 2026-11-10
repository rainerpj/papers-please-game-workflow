"""Project-local improvement ledger. No model, scheduler, or chat-store dependency."""
import argparse
import base64
import copy
from datetime import datetime, timezone, timedelta
from pathlib import Path
import tempfile
import os
import uuid
import json
import workflow as w

NAME = '工作流改进.json'
LABELS = dict(candidate='待核实', verified='已核实', editing='修改中', trial='试用中 · 待观察',
              effective='已观察到改善', ineffective='未达到预期', reverting='正在恢复 · 需继续',
              reverted='已撤回', deferred='暂缓', dismissed='无需修改工作流')
CLASSES = ('workflow', 'preference', 'game_requirement', 'environment', 'positive', 'uncertain')
PROTECTED = {'AGENTS.md', '04_本地进度网页/workflow.py', '04_本地进度网页/workflow.json',
             '04_本地进度网页/improvement.py', '04_本地进度网页/server.py'}


def seed():
    return dict(schema=1, revision=0, signals=[], sources={}, items=[], reviews=[],
                last_task_revision=0, cycle=dict(at=w.now(), game_revision=0, signal_count=0),
                policy=dict(tasks=5, days=7), events=[])


def path_for(root):
    return root / '项目记录' / NAME


def load(root):
    path = path_for(root)
    state = w.read_json(path) if path.exists() else seed()
    validate(state, root)
    return state


def validate(state, root):
    if state.get('schema') != 1 or type(state.get('revision')) is not int or state['revision'] < 0:
        raise w.Invalid('改进记录格式不受支持；保留原文件，不自动重置。')
    for field in ('signals', 'items', 'reviews', 'events'):
        if not isinstance(state.get(field), list):
            raise w.Invalid('改进记录列表无效：' + field)
    keys = [s['key'] for s in state['signals']]
    ids = [i['id'] for i in state['items']]
    if len(keys) != len(set(keys)) or len(ids) != len(set(ids)):
        raise w.Invalid('来源或改进编号重复。')
    for s in state['signals']:
        for field in ('key', 'stream', 'locator', 'text', 'at'):
            w.nonempty(s.get(field), '信号 ' + field)
        if s.get('text_kind') not in ('quote', 'summary'):
            raise w.Invalid('说明信号是原话还是摘要。')
    for item in state['items']:
        if item['status'] not in LABELS or item['classification'] not in CLASSES:
            raise w.Invalid('改进状态或分类无效。')
        if not item['signals'] or not set(item['signals']).issubset(keys):
            raise w.Invalid('改进项必须有真实来源记录。')
        w.nonempty(item.get('title'), '改进标题', 200)
        for field in ('snapshot', 'sealed', 'case_path'):
            if item.get(field):
                w.reference_path(root, item[field])
    if not isinstance(state.get('sources'), dict):
        raise w.Invalid('读取位置无效。')
    for key in ('tasks', 'days'):
        if type(state['policy'][key]) is not int or state['policy'][key] < 1:
            raise w.Invalid('复盘间隔须为正整数。')
    datetime.fromisoformat(state['cycle']['at'])


def game(root):
    state = w.read_json(root / '项目记录/state.json')
    w.validate(state, root)
    return state


def due(state, gs, at=None):
    moment = at or datetime.now(timezone.utc)
    events = [e for e in gs['events'] if e.get('completed')]
    cycle = state['cycle']
    recent = [e for e in events if e['revision'] > cycle['game_revision']]
    checkpoints = set(w.config()['checkpoints'].values())
    assessed = {key for item in state['items'] if item['status'] not in ('candidate', 'deferred') for key in item['signals']}
    unseen = [f['id'] for f in gs['feedback'] if f['status'] == 'open' and 'feedback:' + f['id'] not in assessed]
    fresh = len(state['signals']) > cycle['signal_count'] or gs['revision'] > cycle['game_revision']
    reasons = []
    if len(recent) >= state['policy']['tasks']:
        reasons.append('已完成约定数量的任务，需要集中复盘')
    if any(e['task'] in checkpoints for e in recent):
        reasons.append('阶段检查点已验收，需要阶段复盘')
    if fresh and moment - datetime.fromisoformat(cycle['at']) >= timedelta(days=state['policy']['days']):
        reasons.append('距上次复盘已到约定天数，且有新增记录')
    return dict(task_review=[e['task'] for e in events if e['revision'] > state['last_task_revision']],
                cycle_reasons=reasons, unprocessed_feedback=unseen,
                candidates=[i['id'] for i in state['items'] if i['status'] in ('candidate', 'verified')])


def view(state, gs, root):
    result = copy.deepcopy(state)
    result['due'] = due(state, gs)
    result['labels'] = LABELS
    for item in result['items']:
        refs = [item[k] for k in ('case_path', 'snapshot', 'sealed') if item.get(k)]
        refs += [r['path'] for r in item.get('checks', {}).values()]
        refs += [r['path'] for r in item.get('observations', [])]
        item['missing_evidence'] = [p for p in refs if not w.reference_path(root, p).is_file()]
    return result


def checked_record(record, root):
    for key in ('note', 'by', 'at'):
        w.nonempty(record.get(key), '验证 ' + key)
    w.safe_file(root, record.get('path'))


def editable(root, ref):
    # Only project workflow material is eligible for automatic snapshot/rollback.
    if not isinstance(ref, str) or '\\' in ref:
        raise w.Invalid('改动路径使用项目内相对路径和 /。')
    rel = Path(ref)
    allowed = ('.agents/skills/', '01_客户指南/', '02_工作流规范/', '03_Skills/',
               '04_本地进度网页/', '05_项目模板/', '06_推进提示词库/', 'docs/')
    target = (root / rel).resolve()
    if rel.is_absolute() or '..' in rel.parts or not ref.casefold().startswith(tuple(x.casefold() for x in allowed)) or ref.casefold() in {x.casefold() for x in PROTECTED} or not target.is_relative_to(root.resolve()):
        raise w.Invalid('此路径不在小范围自动改进范围内；核心协议/授权规则需单独审阅：' + ref)
    # Resolve aliases too: an in-project link must not bypass protected paths.
    actual = target.relative_to(root.resolve()).as_posix()
    if actual.casefold() in {x.casefold() for x in PROTECTED} or not actual.casefold().startswith(tuple(x.casefold() for x in allowed)):
        raise w.Invalid('实际目标不在允许的自动改进范围内。')
    if any(x.startswith('.') for x in rel.parts if x != '.agents') or target.is_dir():
        raise w.Invalid('不支持此改动路径。')
    return target


def encoded(path):
    if not path.exists():
        return None
    if path.stat().st_size > 2 * 1024 * 1024:
        raise w.Invalid('单文件超过 2 MB，请单独规划改动与恢复。')
    return base64.b64encode(path.read_bytes()).decode('ascii')


def snapshot(root, item, data):
    directory = root / '项目记录/工作流改进证据/变更备份'
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (item['id'] + '-' + uuid.uuid4().hex + '.json')
    w.atomic(target, data)
    return target.relative_to(root).as_posix()


def sealed_change(root, item):
    data = w.read_json(w.safe_file(root, item['sealed']))
    before = w.read_json(w.safe_file(root, item['snapshot']))
    if set(data['files']) != set(item['files']) or set(before['files']) != set(item['files']):
        raise w.Invalid('备份文件范围不一致，停止恢复。')
    for ref, v in data['files'].items():
        editable(root, ref)
        if v['before'] != before['files'][ref]['before']:
            raise w.Invalid('变更前记录不一致，停止恢复。')
        for field in ('before', 'after'):
            if v[field] is not None:
                base64.b64decode(v[field], validate=True)
    return data


def save(state, old, root, op):
    state['revision'] = old['revision'] + 1
    state['events'].append(dict(op=op, at=w.now(), revision=state['revision']))
    validate(state, root)
    folder = root / '项目记录/backups'
    folder.mkdir(exist_ok=True)
    w.atomic(folder / ('improvement-r' + str(old['revision']) + '-' + uuid.uuid4().hex + '.json'), old)
    w.atomic(path_for(root), state)
    return state


def apply(payload, root=w.ROOT):
    with w.locked(path_for(root)):
        state = load(root)
        if type(payload.get('revision')) is not int or payload['revision'] != state['revision']:
            raise w.Invalid('改进记录已变化，请重读后合并。')
        old = copy.deepcopy(state)
        op = payload.get('op')
        item = next((i for i in state['items'] if i['id'] == payload.get('item')), None)
        if op in ('assess', 'prepare', 'seal', 'trial', 'observe', 'decide', 'cancel', 'rollback') and not item:
            raise w.Invalid('找不到改进项。')
        if op == 'ingest':
            stream = payload['stream']
            w.nonempty(stream, '来源流', 200)
            previous = state['sources'].get(stream, {}).get('cursor', '')
            if payload.get('from_cursor', '') != previous:
                raise w.Invalid('来源读取位置已变化，请重读，不能覆盖游标。')
            coverage = payload.get('coverage')
            if coverage not in ('complete', 'partial', 'unavailable'):
                raise w.Invalid('说明本次读取覆盖程度。')
            w.nonempty(payload.get('note'), '读取说明')
            cursor = payload.get('cursor', previous)
            if not isinstance(cursor, str):
                raise w.Invalid('读取位置必须是文本。')
            if coverage == 'unavailable' and (payload.get('entries') or cursor != previous):
                raise w.Invalid('读取失败不能添加信号或推进读取位置。')
            existing = {s['key']: s for s in state['signals']}
            for entry in payload.get('entries', []):
                signal = {k: entry.get(k, '') for k in ('key', 'locator', 'text', 'text_kind', 'at')}
                signal['stream'] = stream
                if signal['key'] in existing:
                    if existing[signal['key']] != signal:
                        raise w.Invalid('同一来源标识的内容发生冲突，请另记更正，不能覆盖原记录。')
                else:
                    state['signals'].append(signal); existing[signal['key']] = signal
            state['sources'][stream] = dict(cursor=cursor, coverage=coverage, note=payload['note'], at=w.now())
        elif op == 'sync_feedback':
            gs = game(root)
            if state['sources'].get('feedback', {}).get('cursor') == str(gs['revision']):
                return state
            existing = {s['key'] for s in state['signals']}
            for f in gs['feedback']:
                key = 'feedback:' + f['id']
                if key not in existing:
                    # Source text stays in state.json; the signal references it.
                    state['signals'].append(dict(key=key, stream='feedback', locator='state.json#' + f['id'],
                                                 text='网页反馈 ' + f['id'] + '（读取原反馈后分类）', text_kind='summary', at=f['at']))
            state['sources']['feedback'] = dict(cursor=str(gs['revision']), coverage='complete', note='仅同步来源索引，尚未判断内容', at=w.now())
        elif op == 'record':
            w.nonempty(payload.get('key'), '改进主题标识', 200)
            refs = payload.get('signals', [])
            if not refs or not set(refs).issubset({s['key'] for s in state['signals']}):
                raise w.Invalid('先捕获来源，再建立改进项。')
            existing = next((i for i in reversed(state['items']) if i['key'] == payload['key']), None)
            new_refs = set(refs) - {s for i in state['items'] if i['key'] == payload['key'] for s in i['signals']}
            if existing and not new_refs:
                return state
            if existing and existing['status'] in ('candidate', 'verified', 'deferred'):
                existing['signals'] = list(dict.fromkeys(existing['signals'] + refs))
                existing['status'] = 'candidate'  # New evidence needs a fresh assessment.
            else:
                state['items'].append(dict(id='I' + str(len(state['items']) + 1), key=payload['key'],
                    title=payload['title'], signals=sorted(new_refs), status='candidate', previous=existing['id'] if existing else '',
                    classification='uncertain', cause='', expected='', observations=[], history=[]))
        elif op == 'assess':
            if item['status'] not in ('candidate', 'deferred'):
                raise w.Invalid('只有待核实或暂缓项可重新归因。')
            for key in ('cause', 'expected'):
                w.nonempty(payload.get(key), key)
            if payload.get('classification') not in CLASSES:
                raise w.Invalid('分类无效。')
            w.safe_file(root, payload.get('case_path'))
            item.update({k: payload[k] for k in ('classification', 'cause', 'expected', 'case_path')})
            item['status'] = 'verified'
        elif op == 'prepare':
            if item['status'] != 'verified' or item['classification'] not in ('workflow', 'preference'):
                raise w.Invalid('先核实为工作流问题或明确项目协作偏好；游戏需求/不明原因不能改规则。')
            files = payload.get('files', [])
            if not files or len(files) != len(set(files)):
                raise w.Invalid('列出明确且不重复的改动文件。')
            item['snapshot'] = snapshot(root, item, dict(files={ref: dict(before=encoded(editable(root, ref))) for ref in files}))
            item['files'] = files
            item['status'] = 'editing'
        elif op == 'seal':
            if item['status'] != 'editing' or item.get('sealed'):
                raise w.Invalid('只能为未封存的修改记录结果；再次修改应新建改进项。')
            data = w.read_json(w.safe_file(root, item['snapshot']))
            for ref, content in data['files'].items():
                content['after'] = encoded(editable(root, ref))
            if all(v['before'] == v['after'] for v in data['files'].values()):
                raise w.Invalid('没有实际修改。')
            item['sealed'] = snapshot(root, item, data)
        elif op == 'trial':
            if item['status'] != 'editing' or not item.get('sealed'):
                raise w.Invalid('先准备备份、实际修改并 seal 封存，再验证试用。')
            data = sealed_change(root, item)
            w.safe_file(root, item['case_path'])
            if any(encoded(editable(root, ref)) != v['after'] for ref, v in data['files'].items()):
                raise w.Invalid('封存后文件又变化，旧验证不能代表当前内容。')
            checks = payload.get('checks', {})
            for key in ('correctness', 'necessity', 'original', 'related'):
                r = checks.get(key, {})
                checked_record(r, root)
                if r.get('result') != 'pass':
                    raise w.Invalid('审查与原场景/相关案例必须通过。')
            if payload.get('validation_mode') not in ('automated', 'scenario_review', 'fresh_context'):
                raise w.Invalid('注明验证是自动测试、同一 AI 情境自查或新上下文行为验证。')
            item.update(status='trial', checks=copy.deepcopy(checks), validation_mode=payload['validation_mode'], trial_at=w.now())
        elif op == 'observe':
            if item['status'] != 'trial' or payload.get('outcome') not in ('effective', 'ineffective'):
                raise w.Invalid('只有试用项可登记实际效果。')
            record = payload.get('observation', {})
            checked_record(record, root)
            w.safe_file(root, item['case_path'])
            for r in item['checks'].values():
                checked_record(r, root)
            data = sealed_change(root, item)
            if any(encoded(editable(root, ref)) != v['after'] for ref, v in data['files'].items()):
                raise w.Invalid('试用后规则又变化，需重新验证其归属与效果。')
            if record.get('kind') not in ('customer', 'usage'):
                raise w.Invalid('需要客户明确反馈或后续相似使用情境；无投诉不算效果证据。')
            if datetime.fromisoformat(record['at']) < datetime.fromisoformat(item['trial_at']):
                raise w.Invalid('效果观察必须发生在本次试用开始后。')
            item['observations'].append(copy.deepcopy(record)); item['status'] = payload['outcome']
        elif op == 'decide':
            if item['status'] not in ('candidate', 'verified', 'deferred') or payload.get('status') not in ('deferred', 'dismissed'):
                raise w.Invalid('已经修改的项目不能跳过恢复而直接关闭。')
            w.nonempty(payload.get('reason'), '处置理由')
            item.update(status=payload['status'], decision=payload['reason'])
        elif op == 'cancel':
            if item['status'] != 'editing' or item.get('sealed'):
                raise w.Invalid('仅能取消尚未封存且未发生修改的准备。')
            w.nonempty(payload.get('reason'), '取消原因')
            before = w.read_json(w.safe_file(root, item['snapshot']))
            if set(before['files']) != set(item['files']):
                raise w.Invalid('备份范围不一致，停止取消。')
            if any(encoded(editable(root, ref)) != value['before'] for ref, value in before['files'].items()):
                raise w.Invalid('文件已有修改，请先核实并 seal，再按需恢复。')
            item.update(status='dismissed', decision=payload['reason'])
        elif op == 'rollback':
            if item['status'] not in ('editing', 'trial', 'effective', 'ineffective', 'reverting') or not item.get('sealed'):
                raise w.Invalid('需有封存后的改动记录才能受控恢复。')
            w.nonempty(payload.get('reason'), '恢复原因')
            data = sealed_change(root, item)
            resuming = item['status'] == 'reverting'
            for ref, v in data['files'].items():
                current = encoded(editable(root, ref))
                if current != v['after'] and not (resuming and current == v['before']):
                    raise w.Invalid('文件存在后续修改，未覆盖任何文件；请人工合并：' + ref)
            item['status'] = 'reverting'
            item['history'].append(dict(op='rollback', reason=payload['reason'], at=w.now()))
            save(state, old, root, 'rollback_started')
            old = copy.deepcopy(state)
            for ref, v in data['files'].items():
                target = editable(root, ref)
                if v['before'] is None:
                    target.unlink(missing_ok=True)
                else:
                    content = base64.b64decode(v['before'], validate=True)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    fd, temp = tempfile.mkstemp(dir=target.parent, prefix='restore-', suffix='.tmp')
                    try:
                        with os.fdopen(fd, 'wb') as f:
                            f.write(content); f.flush(); os.fsync(f.fileno())
                        os.replace(temp, target)
                    finally:
                        Path(temp).unlink(missing_ok=True)
            item['status'] = 'reverted'
        elif op == 'review':
            gs = game(root)
            if payload.get('game_revision') != gs['revision']:
                raise w.Invalid('游戏记录已变化，复盘后重读再登记。')
            w.nonempty(payload.get('note'), '复盘结论')
            scope = payload.get('scope')
            if scope not in ('task', 'cycle'):
                raise w.Invalid('复盘范围为 task 或 cycle。')
            state['last_task_revision'] = gs['revision']
            if scope == 'cycle':
                state['cycle'] = dict(at=w.now(), game_revision=gs['revision'], signal_count=len(state['signals']))
            state['reviews'].append(dict(scope=scope, note=payload['note'], at=w.now(), game_revision=gs['revision']))
        elif op == 'policy':
            state['policy'] = dict(tasks=payload['tasks'], days=payload['days'])
        else:
            raise w.Invalid('不支持的改进操作。')
        if item and op != 'rollback':
            item['history'].append(dict(op=op, at=w.now()))
        if state == old:
            return state
        return save(state, old, root, op)


def main():
    parser = argparse.ArgumentParser(description='捕获、复盘、验证和恢复本项目工作流改进')
    parser.add_argument('command', choices=('show', 'validate', 'apply'))
    parser.add_argument('--payload', type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'apply' and args.payload is None:
            raise w.Invalid('请提供 --payload JSON 文件。')
        state = apply(w.read_json(args.payload)) if args.command == 'apply' else load(w.ROOT)
        result = view(state, game(w.ROOT), w.ROOT) if args.command == 'show' else dict(ok=True, revision=state['revision'])
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    main()
