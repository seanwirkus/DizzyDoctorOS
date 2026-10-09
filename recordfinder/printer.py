"""DizzyDoctor printer management and job dispatch.

Discovers local/network printers on macOS and Windows, and prints generated PDFs
either to a chosen printer destination or via the operating system print dialog.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys

log = logging.getLogger("recordfinder")


def get_printers() -> dict:
    """Return installed printers and the default printer."""
    printers: list[dict] = []
    default = ""

    if sys.platform == "darwin":
        cups_queues: list[str] = []
        try:
            out = subprocess.check_output(["lpstat", "-e"], text=True, errors="replace", stderr=subprocess.DEVNULL)
            cups_queues = [line.strip() for line in out.splitlines() if line.strip()]
        except Exception:
            pass

        if not cups_queues:
            try:
                out = subprocess.check_output(["lpstat", "-a"], text=True, errors="replace", stderr=subprocess.DEVNULL)
                cups_queues = [line.split()[0] for line in out.splitlines() if line.strip()]
            except Exception:
                pass

        default_queue = ""
        try:
            dout = subprocess.check_output(["lpstat", "-d"], text=True, errors="replace", stderr=subprocess.DEVNULL)
            if ":" in dout:
                default_queue = dout.split(":", 1)[1].strip()
        except Exception:
            pass

        display_names: dict[str, str] = {}
        try:
            import AppKit
            for p in AppKit.NSPrinter.printerNames():
                sp = str(p)
                q = sp.replace(" ", "_")
                display_names[q] = sp
        except Exception:
            pass

        for q in cups_queues:
            disp = display_names.get(q, q.replace("_", " "))
            is_def = (q == default_queue)
            printers.append({"id": q, "name": disp, "is_default": is_def})
            if is_def:
                default = q

        if not default and printers:
            default = printers[0]["id"]
            printers[0]["is_default"] = True

    elif sys.platform.startswith("win"):
        try:
            import win32print
            try:
                default = win32print.GetDefaultPrinter() or ""
            except Exception:
                default = ""
            for p in win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS):
                name = str(p[2])
                printers.append({"id": name, "name": name, "is_default": (name == default)})
        except Exception:
            try:
                cmd = ["powershell", "-NoProfile", "-Command",
                       "Get-CimInstance Win32_Printer | Select-Object Name, Default | ConvertTo-Json"]
                res = subprocess.check_output(cmd, text=True, errors="replace", stderr=subprocess.DEVNULL)
                import json
                data = json.loads(res)
                if isinstance(data, dict):
                    data = [data]
                for item in (data or []):
                    name = str(item.get("Name") or "")
                    if name:
                        is_def = bool(item.get("Default"))
                        printers.append({"id": name, "name": name, "is_default": is_def})
                        if is_def:
                            default = name
            except Exception:
                pass

    return {"printers": printers, "default": default}


def print_file(
    pdf_path: str,
    printer: str = "",
    copies: int = 1,
    title: str = "DizzyDoctor Note",
    system_dialog: bool = False,
) -> tuple[bool, str]:
    """Print the given PDF to a printer queue or open the system print dialog.

    Returns (success: bool, message: str).
    """
    if not os.path.isfile(pdf_path):
        return False, f"File not found: {pdf_path}"

    copies = max(1, int(copies or 1))

    if sys.platform == "darwin":
        if system_dialog:
            try:
                abs_path = os.path.abspath(pdf_path)
                script = f'''tell application "Preview"
    activate
    open POSIX file "{abs_path}"
    print document 1 with print dialog
end tell'''
                res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
                if res.returncode == 0:
                    return True, "Opened system print dialog in Preview"
                return False, res.stderr.strip() or "Could not open system print dialog"
            except Exception as ex:
                return False, str(ex)

        if not shutil.which("lpr"):
            return False, "The macOS lpr command is not available."

        cmd = ["lpr"]
        if printer:
            # Match queue id (spaces replaced with _)
            q = printer.replace(" ", "_") if " " in printer else printer
            cmd += ["-P", q]
        if copies > 1:
            cmd += ["-#", str(copies)]
        cmd += ["-T", title, pdf_path]

        try:
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0:
                target = printer or "system default printer"
                return True, f"Print job sent to {target}"
            return False, res.stderr.strip() or "lpr command exited with error"
        except Exception as ex:
            return False, f"Printing failed: {ex}"

    elif sys.platform.startswith("win"):
        if system_dialog:
            try:
                os.startfile(os.path.abspath(pdf_path), "print")
                return True, "Opened system print dialog"
            except Exception as ex:
                return False, str(ex)

        try:
            if printer:
                # printto verb targets the designated printer
                os.startfile(os.path.abspath(pdf_path), "printto", f'"{printer}"')
                return True, f"Print job sent to {printer}"
            else:
                os.startfile(os.path.abspath(pdf_path), "print")
                return True, "Print job sent to default printer"
        except Exception:
            # Fallback to Edge headless print if available
            edge_candidates = [
                os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
                os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
            ]
            edge_bin = next((p for p in edge_candidates if os.path.isfile(p)), None)
            if edge_bin and printer:
                try:
                    cmd = [edge_bin, "--headless", f"--print-to-printer={printer}", os.path.abspath(pdf_path)]
                    res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
                    if res.returncode == 0:
                        return True, f"Print job sent to {printer} via Edge"
                except Exception:
                    pass

        return False, "Could not dispatch print job on Windows."

    else:
        # Generic Unix
        cmd = ["lpr"]
        if printer:
            cmd += ["-P", printer]
        if copies > 1:
            cmd += ["-#", str(copies)]
        cmd += [pdf_path]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True)
            return (res.returncode == 0), (res.stderr.strip() if res.returncode != 0 else "Print job sent")
        except Exception as ex:
            return False, str(ex)
