#!/usr/bin/env python3
"""
check_tunable_sync.py — GAMING_TUNABLES / THP_TUNABLES tablolarının üç
kopyasının birbirinden sapmadığını doğrular.

NEDEN ÜÇ KOPYA VAR
──────────────────
Bu tablo bilerek tekrarlanıyor, import edilmiyor:

  helper-c/ryzenadj_helper.c  → C fast-path (root olarak çalışır)
  root_helper.py              → Python fallback (root olarak çalışır)
  ryzenadj_wrapper.py         → yetkisiz taraf (UI listesi + `recommended`)

İlk ikisi ROOT olarak çalıştığı için, yetkisiz ryzenadj_wrapper.py'den
import etmeleri istenmeyen bir güven bağı yaratırdı: yetkisiz tarafta
düzenlenebilen bir dosya, root'un hangi yola yazacağını belirlerdi. Bu,
tam olarak bu projede daha önce bulunup kapatılan hata sınıfı
(client-supplied gaming_schema / tunables). O yüzden tekrar kasıtlı.

Bedeli ise sessiz sapma riski: birine yeni bir anahtar eklenip
diğerlerine eklenmezse, C helper "unknown gaming setting, skipped" der ve
ayar sessizce uygulanmaz. Bu script o sapmayı yakalar.

Kullanım:
    python3 tools/check_tunable_sync.py        # repo kökünden
Çıkış kodu: 0 = senkron, 1 = sapma var.
"""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def c_table(source: str, name: str) -> dict:
    """helper-c/ryzenadj_helper.c içindeki `static const TunableDef X[]`
    tablosunu {key: (path, is_sysctl)} olarak çıkarır."""
    m = re.search(r"static const TunableDef " + name + r"\[\] = \{(.*?)\n\};", source, re.S)
    if not m:
        raise SystemExit(f"C tablosu bulunamadı: {name}")
    rows = re.findall(r'\{\s*"([^"]+)",\s*"([^"]+)",\s*(\d)\s*\}', m.group(1))
    return {k: (p, int(t)) for k, p, t in rows}


def py_table(path: Path, name: str) -> dict:
    """Bir .py dosyasındaki modül seviyesi sözlük literalini ast ile
    okur (dosyayı import ETMEZ — root_helper.py'yi import etmek yan
    etkileri tetikler)."""
    src = path.read_text()
    m = re.search(r"^" + name + r"\s*=\s*(\{.*?^\})", src, re.S | re.M)
    if not m:
        raise SystemExit(f"Python tablosu bulunamadı: {name} ({path.name})")
    d = ast.literal_eval(m.group(1))
    # `recommended` gibi ek alanlar yok sayılır; karşılaştırma yalnızca
    # güvenlik açısından anlamlı olan (path, type) çifti üzerinden.
    return {k: (v["path"], 1 if v["type"] == "sysctl" else 0) for k, v in d.items()}


def main() -> int:
    c_src = (ROOT / "helper-c" / "ryzenadj_helper.c").read_text()
    failures = 0

    for table in ("GAMING_TUNABLES", "THP_TUNABLES"):
        reference = c_table(c_src, table)
        for py_file in ("root_helper.py", "ryzenadj_wrapper.py"):
            other = py_table(ROOT / py_file, table)
            if reference == other:
                print(f"OK       {table:16} C == {py_file} ({len(reference)} anahtar)")
                continue
            failures += 1
            print(f"SAPMA    {table:16} C != {py_file}")
            for key in sorted(set(reference) | set(other)):
                if reference.get(key) != other.get(key):
                    print(f"           {key}: C={reference.get(key)} {py_file}={other.get(key)}")

    if failures:
        print(f"\n{failures} tabloda sapma var — güncellenmeyen kopya(lar) sessizce "
              f"'unknown setting, skipped' üretecektir.")
        return 1
    print("\nTüm tunable tabloları senkron.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
