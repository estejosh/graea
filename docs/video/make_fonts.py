#!/usr/bin/env python3
"""Fetch Inter + JetBrains Mono (both OFL, latin subset) from npm and write fonts/fonts.css with the
woff2 files embedded as data URIs, so graea.html renders offline. Run once before render.py.
Generated files (fonts/*.woff2, fonts/fonts.css) are gitignored."""
import base64, pathlib, shutil, subprocess, tempfile

here = pathlib.Path(__file__).parent.resolve()
out = here / "fonts"; out.mkdir(exist_ok=True)
FACES = [("Inter", 400, "inter", "inter-latin-400-normal"), ("Inter", 600, "inter", "inter-latin-600-normal"),
         ("Inter", 700, "inter", "inter-latin-700-normal"), ("JBM", 400, "jetbrains-mono", "jetbrains-mono-latin-400-normal"),
         ("JBM", 500, "jetbrains-mono", "jetbrains-mono-latin-500-normal")]
with tempfile.TemporaryDirectory() as tmp:
    subprocess.run(["npm", "init", "-y"], cwd=tmp, check=True, capture_output=True)
    subprocess.run(["npm", "i", "@fontsource/inter", "@fontsource/jetbrains-mono"], cwd=tmp, check=True, capture_output=True)
    css = "/* Inter and JetBrains Mono (SIL OFL 1.1), latin subset, embedded for offline renders. */\n"
    for fam, wt, pkg, f in FACES:
        src = pathlib.Path(tmp) / "node_modules" / "@fontsource" / pkg / "files" / f"{f}.woff2"
        shutil.copy(src, out / src.name)
        b64 = base64.b64encode(src.read_bytes()).decode()
        css += f'@font-face{{font-family:"{fam}";font-weight:{wt};src:url(data:font/woff2;base64,{b64}) format("woff2")}}\n'
(out / "fonts.css").write_text(css)
print("wrote", out / "fonts.css", len(css) // 1024, "KB")
