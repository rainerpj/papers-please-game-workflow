"""Initialize a new customer's records without overwriting an existing project."""
from pathlib import Path
import shutil
import workflow


def main():
    initial = workflow.seed()  # Validate configuration before copying anything.
    target = workflow.ROOT / '项目记录'
    if target.exists() and any(target.iterdir()):
        raise SystemExit('项目记录目录已有内容。为保护成果，初始化已停止。')
    target.mkdir(exist_ok=True)
    for source in (workflow.ROOT / '05_项目模板').glob('*.md'):
        if source.name not in ('使用说明.md', 'CODEX_HANDOFF.md'):
            shutil.copyfile(source, target / source.name)
            if not (target / source.name).is_file():
                raise SystemExit('模板复制失败：' + source.name)
    workflow.atomic(target / 'state.json', initial)
    print('项目记录已初始化；所有任务均未验收。')


if __name__ == '__main__':
    main()
