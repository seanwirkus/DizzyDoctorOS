r"""Put a build on the clinic's server folder so every PC can install it and updates itself to it.

    python deploy\publish_release.py "V:\DizzyDoctor App" dist\DizzyDoctor.exe
        [--office-config-from "%APPDATA%\DizzyDoctor\config.json"]

Copies the .exe (then version.json, so a PC never sees a version whose .exe isn't there yet), the installer and the
read-me. With --office-config-from it also writes config.json - the settings a newly installed PC starts with: that
config minus what belongs to one person or one PC (initials, signature, publishing, AI settings), plus share_folder.
"""
import argparse
import datetime
import hashlib
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from recordfinder.version import VERSION  # noqa: E402

PER_PC = {"initials", "signature", "staff_names", "publish_data", "ai_read_chats", "ai_baa_confirmed", "ai_daily_limit",
          "missing_folder", "per_patient_subfolder"}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for buf in iter(lambda: f.read(4 << 20), b""):
            h.update(buf)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("share")
    ap.add_argument("exe")
    ap.add_argument("--office-config-from")
    a = ap.parse_args()
    os.makedirs(a.share, exist_ok=True)
    exe = os.path.join(a.share, "DizzyDoctor.exe")
    part = exe + ".part"
    shutil.copyfile(a.exe, part)
    digest = sha256(part)
    if digest != sha256(a.exe):
        os.remove(part)
        sys.exit("the copy to the server came out different - nothing published")
    try:
        os.replace(part, exe)
    except PermissionError:
        os.remove(part)
        sys.exit(f"{exe} is in use (someone is running it straight from the server) - nothing published")
    info = {"version": VERSION, "exe": "DizzyDoctor.exe", "sha256": digest, "size": os.path.getsize(exe),
            "published": datetime.datetime.now().isoformat(timespec="seconds"), "from": os.environ.get("COMPUTERNAME", "")}
    tmp = os.path.join(a.share, "version.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(info, f, indent=1)
    os.replace(tmp, os.path.join(a.share, "version.json"))
    for name in ("Install DizzyDoctor.cmd", "README.txt"):
        if os.path.isfile(os.path.join(HERE, name)):       # README.txt is kept on the admin PC, not in git
            shutil.copyfile(os.path.join(HERE, name), os.path.join(a.share, name))
    if a.office_config_from:
        with open(a.office_config_from, encoding="utf-8") as f:
            cfg = json.load(f)
        office = {k: v for k, v in cfg.items() if k not in PER_PC and "key" not in k.lower()}
        office["share_folder"] = os.path.abspath(a.share)
        with open(os.path.join(a.share, "config.json"), "w", encoding="utf-8") as f:
            json.dump(office, f, indent=2)
    print(f"Published DizzyDoctor {VERSION} to {a.share} ({info['size'] / 1e6:.1f} MB, sha256 {digest[:12]}…)")


if __name__ == "__main__":
    main()
