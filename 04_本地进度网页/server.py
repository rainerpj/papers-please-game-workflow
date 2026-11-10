import argparse
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit
import webbrowser

import workflow

ROOT = workflow.ROOT
WEB = Path(__file__).parent / 'web'


def documents():
    roots = ['01_客户指南', '02_工作流规范', '03_Skills', '05_项目模板', '06_推进提示词库', '07_示例与交付验收', '项目记录', 'docs']
    return sorted([p.relative_to(ROOT).as_posix() for name in roots for p in (ROOT / name).rglob('*.md')])


def evidence_files():
    state = workflow.read_json(workflow.STATE)
    refs = set()
    for task in state['tasks']:
        refs.update(e['path'] for e in task['evidence'])
        refs.update(r['path'] for r in task['reviews'].values())
        for d in task.get('dispositions', []):
            refs.add(d['path'])
            if d.get('approval'):
                refs.add(d['approval'])
        for h in task['history']:
            refs.update(e['path'] for e in h.get('evidence', []))
            refs.update(r['path'] for r in h.get('reviews', {}).values())
    refs.update(r['manifest'] for r in state.get('releases', []))
    return refs


class Handler(BaseHTTPRequestHandler):
    def send(self, body, status=200, mime='application/json; charset=utf-8'):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)

    def valid_host(self):
        return self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}')

    def do_GET(self):
        if not self.valid_host():
            self.send({'error': '只允许本机访问。'}, 403)
            return
        path = unquote(urlsplit(self.path).path)
        try:
            if path in ('/api/state', '/api/export'):
                state = workflow.read_json(workflow.STATE)
                workflow.validate(state, ROOT)
                self.send(workflow.view(state, ROOT) if path == '/api/state' else state)
            elif path == '/api/catalog':
                self.send(dict(project_key=str(ROOT.resolve()), config=workflow.config(), documents=documents(), prompts=workflow.read_json(Path(__file__).with_name('prompts.json'))))
            elif path.startswith('/docs/'):
                ref = path[len('/docs/'):]
                if ref not in documents():
                    self.send({'error': '文档不在允许列表中。'}, 404)
                else:
                    self.send(workflow.safe_file(ROOT, ref).read_bytes(), mime='text/plain; charset=utf-8')
            elif path.startswith('/evidence/'):
                ref = path[len('/evidence/'):]
                if ref not in evidence_files():
                    self.send({'error': '文件未登记为当前证据。'}, 404)
                else:
                    file = workflow.safe_file(ROOT, ref)
                    suffix = file.suffix.lower()
                    if suffix not in ('.md', '.txt', '.log', '.json', '.png', '.jpg', '.jpeg'):
                        self.send({'error': '此文件请在本地查看：' + ref}, 415)
                    elif file.stat().st_size > 8 * 1024 * 1024:
                        self.send({'error': '证据超过 8 MB，请在本地查看：' + ref}, 413)
                    else:
                        mime = mimetypes.guess_type(file.name)[0] if suffix in ('.png', '.jpg', '.jpeg') else 'text/plain; charset=utf-8'
                        self.send(file.read_bytes(), mime=mime)
            elif path in ('/', '/index.html', '/app.js', '/style.css'):
                file = WEB / ('index.html' if path == '/' else path[1:])
                mime = {'css':'text/css', 'js':'text/javascript', 'html':'text/html'}[file.suffix[1:]]
                self.send(file.read_bytes(), mime=mime + '; charset=utf-8')
            else:
                self.send({'error': '找不到这个页面。'}, 404)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.send({'error': '读取失败，请保留记录并让 AI 检查：' + str(exc)}, 422)

    def do_POST(self):
        if not self.valid_host() or self.headers.get('Origin') not in (f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'):
            self.send({'error': '请从本机网页提交。'}, 403)
            return
        if self.path != '/api/feedback' or self.headers.get('Content-Type') != 'application/json':
            self.send({'error': '不支持的请求。'}, 400)
            return
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 20000:
                raise workflow.Invalid('反馈太长或为空。')
            payload = json.loads(self.rfile.read(size))
            # Browser can append feedback only; never accept an operation supplied by a page.
            payload = {k: payload.get(k) for k in ('revision', 'task', 'text', 'kind')}
            payload['op'] = 'feedback'
            state = workflow.apply(payload, workflow.STATE, ROOT)
            self.send(workflow.view(state, ROOT))
        except (ValueError, KeyError, TypeError, OSError) as exc:
            self.send({'error': str(exc)}, 409)

    def log_message(self, format, *args):
        # Keep local request logs free of feedback content and paths.
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--open', action='store_true')
    args = parser.parse_args()
    try:
        server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    except OSError:
        parser.exit(1, '端口无法使用。若已有本项目窗口，请使用它；或运行 server.py --port 8766 --open。\n')
    url = f'http://127.0.0.1:{server.server_port}'
    print('本地进度网页：' + url + '\n关闭此窗口或按 Ctrl+C 停止服务。', flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
