# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（macOS / Linux / Windows 通用）。

用法（在仓库根目录执行）:
    pyinstaller packaging/smart_table_hub.spec --noconfirm
"""

import os
import sys

sys.path.insert(0, os.path.abspath(SPECPATH + "/.."))
from version import __version__, APP_NAME, BUNDLE_ID

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
EXE_NAME = "SmartTableHub"

if sys.platform == "darwin":
    ICON = os.path.join(ROOT, "assets", "app_icon.icns")
elif sys.platform.startswith("win"):
    ICON = os.path.join(ROOT, "assets", "app_icon.ico")
else:
    ICON = None

a = Analysis(
    [os.path.join(ROOT, "smart_table_quick_analysing_hub.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[(os.path.join(ROOT, "assets"), "assets")],
    hiddenimports=[
        "openpyxl", "xlrd", "python_calamine",
        "PIL", "PIL.Image", "PIL.ImageQt",
        "certifi",
    ],
    excludes=[
        # 只保留 PyQt6 一套 Qt 绑定（Anaconda 环境常同时装有 PyQt5）
        "PyQt5", "PySide2", "PySide6",
        "tkinter", "matplotlib.tests", "pandas.tests",
        # Anaconda 环境常见的无关重型包
        "IPython", "jupyter_client", "jupyter_core", "nbformat",
        "notebook", "zmq", "black", "yapf_third_party", "uvloop",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name=EXE_NAME,
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=ICON,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name=EXE_NAME,
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=f"{EXE_NAME}.app",
        icon=ICON,
        bundle_identifier=BUNDLE_ID,
        info_plist={
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleShortVersionString": __version__,
            "CFBundleVersion": __version__,
            "NSHighResolutionCapable": True,
            "NSHumanReadableCopyright": "© 2026 StellarStar255",
            "LSMinimumSystemVersion": "11.0",
        },
    )
