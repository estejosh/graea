#!/usr/bin/env python3
"""Cut Josh's raw recording into the two cuts, clean it, and emit cues + captions.

Inputs : raw recording (m4a/wav), transcript.json (faster-whisper word timestamps)
Outputs: out/audio_A.m4a, out/audio_B.m4a, cues.js, graea-demo.srt, graea-demo-b.srt

Edits are limited to dropping flubbed false starts and the duplicate end-card take,
joined with short crossfades. Nothing is re-spoken or retimed inside a kept range,
and captions are built from the words actually spoken (never from the script).

usage: build_audio.py RAW_RECORDING TRANSCRIPT_JSON
"""
import json, re, subprocess, sys, wave
from pathlib import Path
import numpy as np

HERE = Path(__file__).parent
OUT = HERE / "out"
OUT.mkdir(exist_ok=True)
SR = 48000
XF = int(0.012 * SR)          # 12 ms crossfade at seams
HOLD = 1.2                    # seconds of end-card hold after the last word

# (source_start, source_end, silence_before_s). Boundaries sit inside silences.
RANGES = {
    "A": [(1.00, 14.50, 0.0), (17.45, 40.80, 0.40), (43.90, 60.35, 0.20), (62.00, 82.55, 0.25)],
    "B": [(85.70, 108.00, 0.0), (112.20, 118.30, 0.30)],
}

# named moments (source seconds) the storyboard syncs to
MARKS = {
    "A": dict(l1=1.46, works=4.98, l2=6.44, tap=8.0, nothing=9.3, l3=10.96, shot=11.4, paste=12.5,
              explain=13.64, l4=17.8, eyes=19.72, l5=20.9, msgs=23.12, taps=25.04, btn=25.98,
              l6=27.4, shot2=27.82, renders=29.96, vision=31.44, choose=33.0, one=33.5,
              l7=34.78, order=36.24, saw=37.34, cb=38.72, answered=39.4,
              l8=44.18, trunc=45.44, miss=46.88, catches=48.44,
              l9=50.14, duck=51.82, log=53.26, run1=55.24, fail1=56.46,
              l10=57.64, mcp=59.52, tools=60.2, reads=62.7, patches=63.88, bot=64.46,
              l11=65.78, run2=67.1, fail2=68.02, l12=69.58, passed=70.74, diff=71.5,
              noh=73.22, relay=73.86, l13=76.56, src=77.4, avail=77.68, l14=79.14, ufl=80.82, way=81.9),
    "B": dict(l1=86.33, works=87.87, btn=88.85, nothing=89.81,
              l2=91.07, shot=91.73, reads=92.71, l3=93.83, duck=95.35,
              l4=96.49, exactly=97.35, broke=98.07, where=98.79,
              l5=99.67, fixes=100.11, again=101.61, l6=102.67, run1=103.17, run3=103.83, pass_=104.49,
              noh=105.75, relay=106.37, l7=112.65, src=113.69, usu=115.07, ufl=116.57, way=117.65),
}

CAPTIONS = {
    "A": ["your ai built a telegram bot.", "it says it works.", "you open telegram,", "you tap the button,",
          "nothing happens.", "so you screenshot it,", "paste it back, and explain.",
          "graea gives your ai its own eyes.", "it logs in as a test user", "and messages your bot.",
          "it taps the inline button", "like a person would.", "it screenshots the chat",
          "as telegram renders it.", "a vision model reads it,", "you choose which one.",
          "expected an order summary,", "saw nothing,", "the callback never answered.",
          "markdown, truncated buttons,", "missing captions.", "it catches those.",
          "every step goes into duckdb log.", "run one, three failures.", "your ai plugs in over mcp,",
          "the ai reads what broke", "and patches the bot.", "graea runs it again.",
          "run two, one failure.", "run three, all passed.", "the diff shows the progress.",
          "no human relaying screenshots.", "graea, source available.", "usufruct licensing.",
          "ufl all the way."],
    "B": ["your ai says the bot works.", "the button does nothing.", "graea taps it, screenshots it,",
          "and reads it.", "it logs every step in duckdb.", "it tells your ai exactly what broke and where.",
          "the ai fixes it.", "graea checks again.", "run one, three failures.", "run three, all pass.",
          "no human relaying screenshots.", "graea, source available.", "usufruct licensing.",
          "ufl all the way."],
}


def norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def srt_time(t):
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def main(raw, transcript):
    wav = OUT / "raw48.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", raw, "-ar", str(SR), "-ac", "1", str(wav)], check=True)
    w = wave.open(str(wav))
    audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    room = audio[int(0.15 * SR):int(0.95 * SR)]            # room tone from the leading silence
    words_src = [x for seg in json.load(open(transcript)) for x in seg["words"]]

    cues = {}
    for cut, ranges in RANGES.items():
        pieces, mapping, cursor = [], [], 0.0
        for i, (a, b, gap) in enumerate(ranges):
            if gap > 0:
                n = int(gap * SR)
                tone = np.resize(room, n + XF)
                pieces.append(tone)
                cursor += gap
            seg = audio[int(a * SR):int(b * SR)]
            if gap > 0:
                cursor -= XF / SR          # crossfade overlap at this seam
            mapping.append((a, b, cursor))
            pieces.append(seg)
            cursor += b - a
        out = pieces[0]
        for p in pieces[1:]:
            fade = np.linspace(0, 1, XF)
            out = np.concatenate([out[:-XF], out[-XF:] * (1 - fade) + p[:XF] * fade, p[XF:]])
        # crossfade eats XF samples per seam; correct mapping drift is < 12 ms per seam, negligible
        dur = len(out) / SR + HOLD
        out = np.concatenate([out, np.resize(room, int(HOLD * SR)) * 0.5])

        def m(t):
            for a, b, c in mapping:
                if a <= t <= b:
                    return round(c + (t - a), 3)
            return None

        raw_wav = OUT / f"edit_{cut}.wav"
        with wave.open(str(raw_wav), "wb") as o:
            o.setnchannels(1); o.setsampwidth(2); o.setframerate(SR)
            o.writeframes((np.clip(out, -1, 1) * 32767).astype(np.int16).tobytes())
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw_wav), "-af",
                        f"highpass=f=70,loudnorm=I=-16:TP=-1.5:LRA=11,afade=t=out:st={dur - 1.0:.2f}:d=1.0",
                        "-c:a", "aac", "-b:a", "192k", str(OUT / f"audio_{cut}.m4a")], check=True)

        kept = [x for x in words_src if m(x["s"]) is not None and m(x["e"]) is not None]
        # captions: consume kept words sequentially, strictly matching what was said
        caps, i = [], 0
        for text in CAPTIONS[cut]:
            toks = text.split()
            chunk = kept[i:i + len(toks)]
            got = norm("".join(x["w"] for x in chunk))
            assert got == norm(text), (cut, text, got)
            caps.append(dict(t=text, s=m(chunk[0]["s"]), e=m(chunk[-1]["e"])))
            i += len(toks)
        assert i == len(kept), (cut, "unconsumed words", [x["w"] for x in kept[i:]])
        for k, c in enumerate(caps):                       # hold each caption a beat, never overlap the next
            nxt = caps[k + 1]["s"] if k + 1 < len(caps) else dur
            c["e"] = round(min(c["e"] + 0.45, nxt - 0.04), 3)
        marks = {k: m(v) for k, v in MARKS[cut].items()}
        assert all(v is not None for v in marks.values()), (cut, [k for k, v in marks.items() if v is None])
        cues[cut] = dict(dur=round(dur, 3), marks=marks, caps=caps)
        srt = HERE / ("graea-demo.srt" if cut == "A" else "graea-demo-b.srt")
        srt.write_text("".join(f"{n}\n{srt_time(c['s'])} --> {srt_time(c['e'])}\n{c['t']}\n\n"
                               for n, c in enumerate(caps, 1)))
        print(cut, "duration", round(dur, 1), "s;", len(caps), "captions")
    (HERE / "cues.js").write_text("window.CUES=" + json.dumps(cues, indent=1) + ";\n")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
