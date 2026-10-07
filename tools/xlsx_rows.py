"""xlsx のシートを行ごとに読む（標準ライブラリだけ。大きな表も少しずつ読む）。

使い方: for row in rows("表.xlsx", "シート名"): ...（row は文字列・数値・None の list。1 行目は見出し）
"""
import re
import zipfile
from xml.etree.ElementTree import iterparse

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


def _sheet_path(z: zipfile.ZipFile, name: str) -> str:
    wb = z.read("xl/workbook.xml").decode("utf-8")
    rels = z.read("xl/_rels/workbook.xml.rels").decode("utf-8")
    m = re.search(r'<sheet [^>]*name="' + re.escape(name) + r'"[^>]*r:id="([^"]+)"', wb)
    if not m:
        names = re.findall(r'name="([^"]+)"', wb)
        raise KeyError(f"シート {name} がない: {names}")
    target = re.search(r'Id="' + re.escape(m.group(1)) + r'"[^>]*Target="([^"]+)"', rels)
    if not target:
        target = re.search(r'Target="([^"]+)"[^>]*Id="' + re.escape(m.group(1)) + r'"', rels)
    path = target.group(1).lstrip("/")
    return path if path.startswith("xl/") else "xl/" + path


def _shared(z: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in z.namelist():
        return []
    out = []
    with z.open("xl/sharedStrings.xml") as f:
        for _ev, el in iterparse(f):
            if el.tag == _NS + "si":
                out.append("".join(t.text or "" for t in el.iter(_NS + "t")))
                el.clear()
    return out


def _col(ref: str) -> int:
    n = 0
    for ch in ref:
        if ch.isalpha():
            n = n * 26 + ord(ch.upper()) - 64
        else:
            break
    return n - 1


def rows(path, sheet: str):
    with zipfile.ZipFile(path) as z:
        shared = _shared(z)
        with z.open(_sheet_path(z, sheet)) as f:
            for _ev, el in iterparse(f):
                if el.tag != _NS + "row":
                    continue
                row: list = []
                for c in el.iter(_NS + "c"):
                    i = _col(c.get("r", "A"))
                    while len(row) < i:
                        row.append(None)
                    t, v = c.get("t"), c.find(_NS + "v")
                    if t == "s" and v is not None:
                        val = shared[int(v.text)]
                    elif t == "inlineStr":
                        val = "".join(x.text or "" for x in c.iter(_NS + "t"))
                    elif v is None or v.text is None:
                        val = None
                    elif t in ("str", "e"):
                        val = v.text
                    elif t == "b":
                        val = v.text == "1"
                    else:
                        try:
                            val = float(v.text)
                        except ValueError:
                            val = v.text
                    row.append(val)
                el.clear()
                yield row
