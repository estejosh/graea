#!/usr/bin/env python3
"""Render a cut: Playwright (Chromium) seeks graea.html frame by frame, ffmpeg encodes + muxes audio.
usage: render.py CUT W H CAPS(0|1) OUT.mp4 [--tc] [--silent]
Frames are piped to ffmpeg (nothing written to disk). Audio comes from out/audio_<CUT>.m4a (build_audio.py)."""
import json, pathlib, subprocess, sys
from playwright.sync_api import sync_playwright

here = pathlib.Path(__file__).parent.resolve()
cut, w, h, caps, outp = sys.argv[1].upper(), int(sys.argv[2]), int(sys.argv[3]), sys.argv[4], sys.argv[5]
tc = '--tc' in sys.argv; silent = '--silent' in sys.argv
FPS = 30
cues = json.loads((here / 'cues.js').read_text()[len('window.CUES='):].rsplit(';', 1)[0])
dur = cues[cut]['dur']; n = int(dur * FPS)
out = here / 'out'; out.mkdir(exist_ok=True)
cmd = ['ffmpeg', '-y', '-loglevel', 'error', '-f', 'image2pipe', '-framerate', str(FPS), '-c:v', 'mjpeg', '-i', '-']
if not silent: cmd += ['-i', str(out / f'audio_{cut}.m4a')]
cmd += ['-c:v', 'libx264', '-crf', '18', '-pix_fmt', 'yuv420p', '-movflags', '+faststart']
if not silent: cmd += ['-c:a', 'copy', '-shortest']
cmd += [str(out / outp)]
ff = subprocess.Popen(cmd, stdin=subprocess.PIPE)
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page(viewport={'width': w, 'height': h}, device_scale_factor=1)
    pg.goto(f'file://{here}/graea.html?cut={cut}&w={w}&h={h}&caps={caps}&tc={int(tc)}')
    pg.wait_for_function('window.READY===true')
    for f in range(n):
        pg.evaluate(f'seek({f / FPS})')
        ff.stdin.write(pg.screenshot(type='jpeg', quality=92))
    b.close()
ff.stdin.close(); ff.wait()
print('done', outp, n, 'frames', round(dur, 1), 's')
