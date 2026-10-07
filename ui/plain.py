"""Plain adapter: asynchronous line input without full-screen terminal control."""
from __future__ import annotations
import asyncio
import os
import sys
from contextlib import contextmanager
from dataclasses import replace
from rich.console import Console as RichConsole
from ui.console import Console
from ui.theme import load_theme

class PlainPromptSession:
    def __init__(self,input_stream=None,output_stream=None):
        self.input=input_stream or sys.stdin;self.output=output_stream or sys.stdout
        self.buffer=b'';self.eof=False
    async def prompt_async(self,message,*,default=None,bottom_toolbar=None):
        self.output.write(message+(f'[保留输入：{default}] ' if message=='> ' and default else ''));self.output.flush()
        loop=asyncio.get_running_loop(); fd=self.input.fileno()
        while b'\n' not in self.buffer:
            if self.eof:
                if self.buffer: break
                raise EOFError()
            ready=loop.create_future()
            def readable():
                try:
                    chunk=os.read(fd,4096)
                    self.buffer+=chunk
                    if not chunk: self.eof=True
                    if not ready.done(): ready.set_result(None)
                except OSError as error:
                    if not ready.done(): ready.set_exception(error)
            try:
                loop.add_reader(fd,readable)
                await ready
            finally: loop.remove_reader(fd)
        line,sep,self.buffer=self.buffer.partition(b'\n')
        text=line.decode(self.input.encoding or 'utf-8',errors='replace').rstrip('\r')
        return text or default or ''

class PlainConsole(Console):
    def __init__(self):
        super().__init__(theme=load_theme(ascii_only=True),console=RichConsole(no_color=True,highlight=False))
    def apply_theme(self, theme):
        super().apply_theme(replace(theme, glyph_running=">", glyph_ok="+", glyph_fail="!", gutter="|", bullet="-"))
    def resume_live(self): pass
    def pause_live(self): pass

async def run_plain(settings,**options):
    from ui.app import run_inline
    await run_inline(settings,prompt_session=PlainPromptSession(),console=PlainConsole(),plain=True,**options)
