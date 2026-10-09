r"""Write the Windows version resource for DizzyDoctor.exe, so Explorer, Task Manager and the file's Properties
show "DizzyDoctor" and the version instead of a bare file name.

    python deploy\version_info.py build\version_info.txt
    python -m PyInstaller ... --version-file build\version_info.txt ...
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from recordfinder.version import VERSION  # noqa: E402

TEMPLATE = """VSVersionInfo(
  ffi=FixedFileInfo(filevers={nums}, prodvers={nums}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('FileDescription', 'DizzyDoctor'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', 'DizzyDoctor'),
      StringStruct('OriginalFilename', 'DizzyDoctor.exe'),
      StringStruct('ProductName', 'DizzyDoctor'),
      StringStruct('ProductVersion', '{version}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def main() -> None:
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join("build", "version_info.txt")
    parts = [int(x) for x in VERSION.split(".") if x.isdigit()][:4]
    nums = tuple(parts + [0] * (4 - len(parts)))
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(TEMPLATE.format(nums=nums, version=VERSION))
    print(f"{out}: DizzyDoctor {VERSION}")


if __name__ == "__main__":
    main()
