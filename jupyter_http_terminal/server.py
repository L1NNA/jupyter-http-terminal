#!/usr/bin/env python3
import os
import uuid
import pty
import select
import subprocess
import asyncio
import logging
import termios
import tty
import fcntl
import struct
import signal
import argparse

from aiohttp import web

# Parse command-line arguments for debug flag
def parse_args():
    parser = argparse.ArgumentParser(description="HTTP terminal server")
    parser.add_argument(
        '--debug', action='store_true', help='Enable debug logging (INFO & DEBUG levels)'
    )
    return parser.parse_args()

# Only parse args when running as script
if __name__ != '__main__':
    args = None
else:
    args = parse_args()

# Configure root logger: INFO by default, DEBUG if requested
root_level = logging.DEBUG if getattr(args, 'debug', False) else logging.INFO
logging.basicConfig(
    level=root_level,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
)
logger = logging.getLogger(__name__)

# Suppress HTTP access logs (keep only errors), retain application logs
logging.getLogger('aiohttp.access').setLevel(logging.ERROR)
logging.getLogger('aiohttp.server').setLevel(logging.ERROR)
logging.getLogger('aiohttp.web').setLevel(logging.ERROR)

# For Jupyter Server Proxy integration
def setup_jupyter_server_proxy():
    return {
        'command': ['python', '-m', 'jupyter_http_terminal.server'],
        'port': 8866,
        'absolute_url': False,
        'timeout': 30,
        'new_browser_window': True,
        'launcher_entry': {
            'title': 'HTTP Terminal',
            'icon_path': os.path.join(
                os.path.dirname(__file__), 'icons', 'capybara.svg'
            )
        }
    }

SRC_DIR = os.path.dirname(os.path.abspath(__file__))


class TerminalSession:
    """One PTY + one headless tmux server + one tmux client per session_id."""
    def __init__(self, session_name, rows=24, cols=80):
        self.session_name = session_name
        self.master, self.slave = pty.openpty()
        self._set_pty_size(rows, cols)

        # Detached tmux server
        subprocess.run(
            ['tmux', 'new-session', '-d', '-s', session_name],
            env=dict(os.environ, TERM='xterm-256color'),
            check=False
        )

        # Attach client to that session on our PTY
        self.process = subprocess.Popen(
            ['tmux', 'attach-session', '-t', session_name],
            stdin=self.slave,
            stdout=self.slave,
            stderr=self.slave,
            start_new_session=True,
            env=dict(os.environ, TERM='xterm-256color')
        )

        # Put the master side into raw mode
        tty.setraw(self.master)

    def _set_pty_size(self, rows, cols):
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        try:
            fcntl.ioctl(self.master, termios.TIOCSWINSZ, winsize)
        except Exception as e:
            logger.error(f"PTY ioctl resize failed: {e}")

    def resize(self, rows, cols):
        self._set_pty_size(rows, cols)
        try:
            pgid = os.getpgid(self.process.pid)
            os.killpg(pgid, signal.SIGWINCH)
        except Exception as e:
            logger.error(f"Failed to send SIGWINCH to tmux client (pgid={pgid}): {e}")

    def close(self):
        try:
            self.process.terminate()
            self.process.wait(timeout=1)
        except Exception:
            pass
        subprocess.run(['tmux', 'kill-session', '-t', self.session_name], check=False)


class TerminalServer:
    def __init__(self):
        self.app = web.Application()
        self.sessions = {}  # session_id → TerminalSession
        self.setup_routes()

    def setup_routes(self):
        self.app.router.add_get('/',                self.handle_index)
        self.app.router.add_get('/terminal',        self.handle_new)
        self.app.router.add_get('/terminal/output', self.handle_poll)
        self.app.router.add_post('/terminal/input',  self.handle_input)
        self.app.router.add_post('/terminal/resize', self.handle_resize)

    async def handle_index(self, request):
        return web.FileResponse(os.path.join(SRC_DIR, 'static/index.html'))

    async def handle_new(self, request):
        sid = request.query.get('session_id')
        if not sid:
            raise web.HTTPBadRequest(text="Missing session_id")
        if sid not in self.sessions:
            self.sessions[sid] = TerminalSession(session_name=sid)
            logger.info(f"🆕 Started tmux session '{sid}'")
        return web.json_response({'status': 'ok'})

    def _get_session(self, request):
        sid = request.query.get('session_id')
        if not sid or sid not in self.sessions:
            raise web.HTTPBadRequest(text="Invalid or missing session_id")
        return sid, self.sessions[sid]

    async def handle_input(self, request):
        _, sess = self._get_session(request)
        data = await request.json()
        buf = data.get('input', '').encode()
        if buf == b'\r': buf = b'\n'
        os.write(sess.master, buf)
        return web.json_response({'status': 'ok'})

    async def handle_resize(self, request):
        _, sess = self._get_session(request)
        data = await request.json()
        sess.resize(int(data.get('rows', 24)), int(data.get('cols', 80)))
        return web.json_response({'status': 'ok'})

    async def handle_poll(self, request):
        """Poll for any available output, and flag closed if tmux client died."""
        sid, sess = self._get_session(request)
        output_parts = []

        while True:
            r, _, _ = select.select([sess.master], [], [], 0)
            if not r:
                break
            chunk = os.read(sess.master, 4096)
            if not chunk:
                break
            output_parts.append(chunk.decode('utf-8', errors='ignore'))

        output = ''.join(output_parts)
        closed = sess.process.poll() is not None

        if closed:
            # final non-blocking drain
            while True:
                r, _, _ = select.select([sess.master], [], [], 0)
                if not r:
                    break
                try:
                    chunk = os.read(sess.master, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                output += chunk.decode('utf-8', errors='ignore')

            sess.close()
            del self.sessions[sid]
            logger.info(f"🗑️  Session '{sid}' closed and cleaned up")

        return web.json_response({
            'output': output,
            'closed': closed
        })

    async def cleanup(self):
        for sid, sess in list(self.sessions.items()):
            sess.close()
            logger.info(f"🗑️  Closed session '{sid}' on shutdown")
        self.sessions.clear()


async def main():
    server = TerminalServer()
    runner = web.AppRunner(server.app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8866)
    await site.start()
    logger.info("🚀 HTTP terminal server listening on http://0.0.0.0:8866")
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        logger.info("🔌 Shutdown requested")
    finally:
        await server.cleanup()
        await runner.cleanup()


if __name__ == '__main__':
    asyncio.run(main())
