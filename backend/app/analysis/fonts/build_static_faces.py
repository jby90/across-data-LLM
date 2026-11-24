"""Instantiate static Regular (400) and Bold (700) TTF faces from bundled OFL-1.1
variable fonts during Docker build so headless LibreOffice renders Regular/Bold
multi-script weights without modifying SHA-256-verified files in app/analysis/fonts/."""

from __future__ import annotations

import pathlib
import shutil
import sys

from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

FAMILIES = (
    ("notosanssc", "NotoSansSC[wght].ttf", "Noto Sans SC", "NotoSansSC"),
    ("notosanskr", "NotoSansKR[wght].ttf", "Noto Sans KR", "NotoSansKR"),
    ("notosansthai", "NotoSansThai[wdth,wght].ttf", "Noto Sans Thai", "NotoSansThai"),
    ("notosansbengali", "NotoSansBengali[wdth,wght].ttf", "Noto Sans Bengali", "NotoSansBengali"),
    ("notosanshebrew", "NotoSansHebrew[wdth,wght].ttf", "Noto Sans Hebrew", "NotoSansHebrew"),
    ("notonaskharabic", "NotoNaskhArabic[wght].ttf", "Noto Naskh Arabic", "NotoNaskhArabic"),
)


def _instantiate_one(
    src_ttf: pathlib.Path, out_dir: pathlib.Path, family_name: str, ps_stem: str
) -> None:
    for weight, style_name in ((400, "Regular"), (700, "Bold")):
        base_tt = TTFont(str(src_ttf))
        axes: dict[str, float] = {"wght": weight}
        if "fvar" in base_tt and any(a.axisTag == "wdth" for a in base_tt["fvar"].axes):
            axes["wdth"] = 100.0
        tt = instantiateVariableFont(base_tt, axes, inplace=False)
        if "STAT" in tt:
            del tt["STAT"]
        tt["OS/2"].usWeightClass = weight
        if weight >= 700:
            tt["OS/2"].fsSelection = (tt["OS/2"].fsSelection & ~0x40) | 0x20
            tt["head"].macStyle |= 0x01
        else:
            tt["OS/2"].fsSelection = (tt["OS/2"].fsSelection & ~0x21) | 0x40
            tt["head"].macStyle &= ~0x01
        full_label = f"{family_name} {style_name}" if style_name != "Regular" else family_name
        for rec in list(tt["name"].names):
            if rec.nameID in (1, 16):
                new_val = family_name
            elif rec.nameID in (2, 17):
                new_val = style_name
            elif rec.nameID == 4:
                new_val = full_label
            elif rec.nameID == 6:
                new_val = f"{ps_stem}-{style_name}"
            else:
                continue
            tt["name"].setName(new_val, rec.nameID, rec.platformID, rec.platEncID, rec.langID)
        tt.save(str(out_dir / f"{ps_stem}-{style_name}.ttf"))


def build_static_faces(src_dir: pathlib.Path, out_dir: pathlib.Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    root = src_dir.parent if src_dir.name == "notosanssc" else src_dir
    for subdir, fn, fam_name, ps_stem in FAMILIES:
        ttf = root / subdir / fn
        ofl = root / subdir / "OFL.txt"
        if not ttf.exists():
            continue
        if ofl.exists():
            shutil.copy2(ofl, out_dir / f"OFL-{subdir}.txt")
        _instantiate_one(ttf, out_dir, fam_name, ps_stem)


if __name__ == "__main__":
    s_dir = pathlib.Path(sys.argv[1])
    o_dir = pathlib.Path(sys.argv[2])
    build_static_faces(s_dir, o_dir)
    if s_dir.name in ("notosanssc", "fonts"):
        shutil.rmtree(s_dir.parent, ignore_errors=True)
    else:
        shutil.rmtree(s_dir, ignore_errors=True)
