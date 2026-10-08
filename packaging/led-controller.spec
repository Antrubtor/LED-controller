# PyInstaller build of LED Controller.
#
#   uv run --group build pyinstaller packaging/led-controller.spec --noconfirm
#
# Windows: dist/LED-Controller.exe (single file)
# Linux:   dist/led-controller (single file)
# macOS:   dist/LED Controller.app

import os
import sys

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))  # noqa: F821 - provided by PyInstaller
NAME = "LED Controller"

# bleak picks its backend at runtime: bundle the one of the build platform only.
BACKEND = {"win32": "winrt", "darwin": "corebluetooth"}.get(sys.platform, "bluezdbus")
OTHER_BACKENDS = {"winrt", "corebluetooth", "bluezdbus", "p4android"} - {BACKEND}
hiddenimports = collect_submodules(
    "bleak", filter=lambda name: not any(f"bleak.backends.{b}" in name for b in OTHER_BACKENDS)
)
binaries = []
hiddenimports += collect_submodules("mss")
if sys.platform == "win32":
    hiddenimports += collect_submodules("winrt")
    hiddenimports += collect_submodules("dxcam") + collect_submodules("comtypes")
    hiddenimports += ["pyaudiowpatch"]
    binaries += collect_dynamic_libs("pyaudiowpatch")

a = Analysis(  # noqa: F821
    [os.path.join(SPECPATH, "launcher.py")],  # noqa: F821
    pathex=[os.path.join(ROOT, "src")],
    binaries=binaries,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821

if sys.platform == "darwin":
    exe = EXE(  # noqa: F821
        pyz, a.scripts, [], exclude_binaries=True, name=NAME, console=False, upx=False,
    )
    coll = COLLECT(exe, a.binaries, a.datas, name=NAME, upx=False)  # noqa: F821
    app = BUNDLE(  # noqa: F821
        coll,
        name=f"{NAME}.app",
        bundle_identifier="io.github.ledcontroller",
        info_plist={
            "NSBluetoothAlwaysUsageDescription": "LED Controller connects to your LED controller over Bluetooth.",
            "NSMicrophoneUsageDescription": "LED Controller can make your LEDs react to a microphone.",
            "NSHighResolutionCapable": True,
        },
    )
else:
    exe = EXE(  # noqa: F821
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="LED-Controller" if sys.platform == "win32" else "led-controller",
        console=False,
        upx=False,
    )
