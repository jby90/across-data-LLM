"""Instantiate static Regular (400) and Bold (700) TTF faces from the bundled OFL-1.1
NotoSansSC[wght].ttf during Docker image build so headless LibreOffice renders crisp
Regular/Bold CJK weights without modifying SHA-256-verified files under app/analysis/fonts/."""

from __future__ import annotations

import pathlib
import shutil
import sys

from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont


def build_static_faces(src_dir: pathlib.Path, out_dir: pathlib.Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_dir / "OFL.txt", out_dir / "OFL.txt")
    src_ttf = str(src_dir / "NotoSansSC[wght].ttf")
    for weight, style_name, out_name in (
        (400, "Regular", "NotoSansSC-Regular.ttf"),
        (700, "Bold", "NotoSansSC-Bold.ttf"),
    ):
        tt = instantiateVariableFont(TTFont(src_ttf), {"wght": weight}, inplace=False)
        if "STAT" in tt:
            del tt["STAT"]
        tt["OS/2"].usWeightClass = weight
        if weight >= 700:
            tt["OS/2"].fsSelection = (tt["OS/2"].fsSelection & ~0x40) | 0x20
            tt["head"].macStyle |= 0x01
        else:
            tt["OS/2"].fsSelection = (tt["OS/2"].fsSelection & ~0x21) | 0x40
            tt["head"].macStyle &= ~0x01
        full_label = f"Noto Sans SC {style_name}" if style_name != "Regular" else "Noto Sans SC"
        for rec in list(tt["name"].names):
            if rec.nameID in (1, 16):
                new_val = "Noto Sans SC"
            elif rec.nameID in (2, 17):
                new_val = style_name
            elif rec.nameID == 4:
                new_val = full_label
            elif rec.nameID == 6:
                new_val = f"NotoSansSC-{style_name}"
            else:
                continue
            tt["name"].setName(new_val, rec.nameID, rec.platformID, rec.platEncID, rec.langID)
        tt.save(str(out_dir / out_name))


if __name__ == "__main__":
    s_dir = pathlib.Path(sys.argv[1])
    o_dir = pathlib.Path(sys.argv[2])
    build_static_faces(s_dir, o_dir)
    shutil.rmtree(s_dir.parent if s_dir.name == "notosanssc" else s_dir, ignore_errors=True)
