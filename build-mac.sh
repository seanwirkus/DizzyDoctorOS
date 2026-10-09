#!/usr/bin/env bash
# Builds DizzyDoctor.app on a Mac (Python 3.12+ from python.org or Homebrew).
#   bash build-mac.sh            # build, sign ad hoc, self-test
#   bash build-mac.sh install    # ...then copy it to ~/Applications
# Outlook sync stays Windows-only; the Mac app reads the clinic data the main PC publishes, and
# charting / dictation / OCR (Apple Vision) run fully on the Mac.
set -euo pipefail
cd "$(dirname "$0")"
VERSION="$(python3 -c 'import recordfinder.version as v; print(v.VERSION)')"
BUILD="${BUILD_DIR:-$HOME/Library/Caches/DizzyDoctor-build}"
mkdir -p "$BUILD"
[[ -x "$BUILD/venv/bin/python" ]] || python3 -m venv "$BUILD/venv"
"$BUILD/venv/bin/pip" install -q --upgrade pip
"$BUILD/venv/bin/pip" install -q -r requirements.txt pyinstaller
"$BUILD/venv/bin/python" -m unittest
SRC="$(pwd)"
# Default launch is main.py -> recordfinder.shell.run (native pywebview). --classic is the Tk app.
# --collect-all webview does not pull PyObjC; the Cocoa backend imports these by name.
"$BUILD/venv/bin/python" - << 'PY'
import importlib
mods = [
    "webview", "webview.platforms.cocoa", "objc", "AppKit", "Foundation", "WebKit",
    "Quartz", "CoreFoundation", "PyObjCTools.AppHelper", "Vision",
]
for name in mods:
    importlib.import_module(name)
    print("import ok", name)
PY
"$BUILD/venv/bin/python" -m PyInstaller --noconfirm --windowed --name DizzyDoctor --osx-bundle-identifier com.dizzydoctor.app \
  --icon "$SRC/assets/DizzyDoctor.icns" \
  --hidden-import webview.platforms.cocoa \
  --hidden-import AppKit \
  --hidden-import Foundation \
  --hidden-import WebKit \
  --hidden-import Quartz \
  --hidden-import CoreFoundation \
  --hidden-import PyObjCTools.AppHelper \
  --hidden-import Vision \
  --collect-submodules keyring \
  --collect-all webview \
  --collect-all objc \
  --collect-all AppKit \
  --collect-all Foundation \
  --collect-all WebKit \
  --collect-all Quartz \
  --collect-all CoreFoundation \
  --collect-all PyObjCTools \
  --collect-all Vision \
  --collect-all pymupdf --collect-all faster_whisper --collect-all ctranslate2 --collect-all av \
  --add-data "$SRC/recordfinder/charting/web:recordfinder/charting/web" \
  --add-data "$SRC/recordfinder/charting/templates:recordfinder/charting/templates" \
  --add-data "$SRC/recordfinder/charting/note_schema.json:recordfinder/charting" \
  --add-data "$SRC/recordfinder/superbill_form.json:recordfinder" \
  --add-data "$SRC/recordfinder/superbill_form.png:recordfinder" \
  --add-data "$SRC/recordfinder/superbill_form_older.json:recordfinder" \
  --add-data "$SRC/recordfinder/superbill_form_older.png:recordfinder" \
  --distpath "$BUILD/dist" --workpath "$BUILD/work" --specpath "$BUILD" main.py
APP="$BUILD/dist/DizzyDoctor.app"
PLIST="$APP/Contents/Info.plist"
cp "$SRC/assets/DizzyDoctor.icns" "$APP/Contents/Resources/DizzyDoctor.icns"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :CFBundleVersion string $VERSION" "$PLIST" 2>/dev/null || /usr/libexec/PlistBuddy -c "Set :CFBundleVersion $VERSION" "$PLIST"
/usr/libexec/PlistBuddy -c "Set :CFBundleIconFile DizzyDoctor" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :NSMicrophoneUsageDescription string 'DizzyDoctor transcribes dictation on this computer; audio never leaves it.'" "$PLIST" 2>/dev/null || true
# Refuse to sign a bundle that is still the Tk front door or missing the shell files.
"$BUILD/venv/bin/python" - "$APP" "$SRC/assets/DizzyDoctor.icns" << 'PY'
import marshal, pathlib, struct, sys, zlib

app = pathlib.Path(sys.argv[1])
icns = pathlib.Path(sys.argv[2])
exe = app / "Contents/MacOS/DizzyDoctor"
web_files = list(app.rglob("recordfinder/charting/web/shell.js"))
note_files = list(app.rglob("recordfinder/charting/web/modules/note.js"))
old_app_js = list(app.rglob("recordfinder/charting/web/app.js"))
icon = app / "Contents/Resources/DizzyDoctor.icns"
problems = []
if not web_files:
    problems.append("shell.js is not in the bundle")
if not note_files:
    problems.append("modules/note.js is not in the bundle")
if old_app_js:
    problems.append("old web/app.js is still in the bundle")
if not icon.is_file() or icon.read_bytes() != icns.read_bytes():
    problems.append("Contents/Resources/DizzyDoctor.icns is not assets/DizzyDoctor.icns")

data = exe.read_bytes()
magic = b"MEI\014\013\012\013\016"
idx = data.rfind(magic)
fmt = "!8sIIII64s"
clen = struct.calcsize(fmt)
_magic, archive_length, toc_offset, toc_length, _pyvers, _pylib = struct.unpack(fmt, data[idx:idx + clen])
start = idx + clen - archive_length
toc = data[start + toc_offset:start + toc_offset + toc_length]
entry_fmt = "!IIIIBc"
elen = struct.calcsize(entry_fmt)
pos = 0
main_blob = None
while pos < len(toc):
    entry_length, entry_offset, data_length, uncompressed_length, compression_flag, typecode = struct.unpack(
        entry_fmt, toc[pos:pos + elen])
    pos += elen
    name = toc[pos:pos + (entry_length - elen)].rstrip(b"\0").decode("utf-8", "replace")
    pos += entry_length - elen
    if name == "main" and typecode == b"s":
        blob = data[start + entry_offset:start + entry_offset + data_length]
        main_blob = zlib.decompress(blob) if compression_flag else blob
if main_blob is None:
    problems.append("frozen main script was not found in the executable")
else:
    code = marshal.loads(main_blob)
    names = set(code.co_names)
    if "recordfinder.shell" not in names:
        problems.append("frozen main does not import recordfinder.shell")
    if "recordfinder.app" not in names:
        problems.append("frozen main does not keep recordfinder.app for --classic")

pyz_at = data.find(b"PYZ\x00")
tocpos = struct.unpack("!I", data[pyz_at + 8:pyz_at + 12])[0]
pyz_toc = dict(marshal.loads(data[pyz_at + tocpos:]))
for mod in ("recordfinder.shell", "webview", "webview.platforms.cocoa", "AppKit", "Foundation", "WebKit", "Vision"):
    if mod not in pyz_toc:
        problems.append(f"PYZ missing {mod}")

if problems:
    print("BUNDLE CHECK FAILED:")
    for item in problems:
        print(" -", item)
    sys.exit(1)
print("bundle check ok:", web_files[0])
PY
codesign --force --deep -s - "$APP"
"$APP/Contents/MacOS/DizzyDoctor" --selftest "$BUILD/selftest.json"
cat "$BUILD/selftest.json"
echo
echo "Built: $APP (version $VERSION)"
if [[ "${1:-}" == "install" ]]; then
  pkill -x DizzyDoctor 2>/dev/null || true
  rm -rf "$HOME/Applications/DizzyDoctor.app"
  ditto "$APP" "$HOME/Applications/DizzyDoctor.app"
  echo "Installed to $HOME/Applications/DizzyDoctor.app"
else
  echo "The app you open is $HOME/Applications/DizzyDoctor.app. This build was not copied there."
  echo "Re-run: bash build-mac.sh install"
fi
