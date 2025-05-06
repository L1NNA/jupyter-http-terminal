import os
import pty
import select
import subprocess
import asyncio
from aiohttp import web
import json
import logging
import termios
import tty
import fcntl
import struct

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class TerminalServer:
    def __init__(self):
        self.app = web.Application()
        self.setup_routes()
        self.setup_terminal()

    def setup_terminal(self):
        """Initialize a single shared terminal session."""
        # Create a new PTY
        self.master, self.slave = pty.openpty()
        
        # Set terminal size
        self.set_terminal_size(24, 80)
        
        # Start bash process
        self.process = subprocess.Popen(
            ['bash', '-c', 'tmux new-session -A -s shared-session'],
            # ['tmux', 'new-session', '-A', '-s', 'shared-session'],
            stdin=self.slave,
            stdout=self.slave,
            stderr=self.slave,
            start_new_session=True,
            env=dict(os.environ, TERM='xterm-256color')
        )
        
        # Set terminal to raw mode
        tty.setraw(self.master)
        
        logger.info("Shared terminal process started")

    def set_terminal_size(self, rows, cols):
        """Set the terminal size."""
        try:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(self.master, termios.TIOCSWINSZ, winsize)
        except Exception as e:
            logger.error(f"Failed to set terminal size: {e}")

    def setup_routes(self):
        self.app.router.add_get('/', self.handle_index)
        self.app.router.add_get('/terminal', self.handle_terminal)
        self.app.router.add_post('/terminal/input', self.handle_terminal_input)
        self.app.router.add_get('/terminal/output', self.handle_terminal_output)
        self.app.router.add_post('/terminal/resize', self.handle_terminal_resize)

    async def handle_index(self, request):
        """Serve the main HTML page with the terminal interface."""
        return web.FileResponse('jupyter_http_terminal/static/index.html')

    async def handle_terminal(self, request):
        """Return the terminal status."""
        return web.json_response({'status': 'ok'})

    async def handle_terminal_input(self, request):
        """Handle input from the terminal interface."""
        data = await request.json()
        input_data = data.get('input', '').encode()
        
        # Handle special keys
        print('!!!', input_data)
        if input_data == b'\r':
            input_data = b'\n'
        
        os.write(self.master, input_data)
        return web.json_response({'status': 'ok'})

    async def handle_terminal_resize(self, request):
        """Handle terminal resize events."""
        data = await request.json()
        rows = data.get('rows', 24)
        cols = data.get('cols', 80)
        self.set_terminal_size(rows, cols)
        return web.json_response({'status': 'ok'})

    async def handle_terminal_output(self, request):
        """Stream output from the terminal to the client."""
        async def stream_output():
            while True:
                try:
                    # Check if there's data to read
                    r, _, _ = select.select([self.master], [], [], 0.1)
                    if r:
                        data = os.read(self.master, 1024)
                        if data:
                            yield data
                    await asyncio.sleep(0.1)
                except Exception as e:
                    logger.error(f"Error in terminal output stream: {e}")
                    break
        
        response = web.StreamResponse()
        response.headers['Content-Type'] = 'application/octet-stream'
        await response.prepare(request)
        
        async for chunk in stream_output():
            await response.write(chunk)
        
        return response

    async def cleanup(self):
        """Clean up the terminal process."""
        if hasattr(self, 'process'):
            self.process.terminate()
            self.process.wait()
            logger.info("Terminal process terminated")

def setup_jupyter_server_proxy():
    """Setup function for Jupyter server proxy."""
    server = TerminalServer()
    return {
        'command': ['python', '-m', 'jupyter_http_terminal.server'],
        'port': 8888,
        'absolute_url': False,
        'timeout': 30
    }

async def main():
    """Main entry point for running the server directly."""
    server = TerminalServer()
    runner = web.AppRunner(server.app)
    await runner.setup()
    site = web.TCPSite(runner, 'localhost', 8888)
    await site.start()
    logger.info("Server started at http://localhost:8888")
    
    try:
        while True:
            await asyncio.sleep(3600)  # Keep the server running
    except KeyboardInterrupt:
        logger.info("Shutting down server...")
    finally:
        await server.cleanup()
        await runner.cleanup()

if __name__ == '__main__':
    asyncio.run(main())

