# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all

webview_datas, webview_binaries, webview_hidden = collect_all("webview")
sd_datas, sd_binaries, sd_hidden = collect_all("sounddevice")
np_datas, np_binaries, np_hidden = collect_all("numpy")
voa_datas, voa_binaries, voa_hidden = collect_all("dvoacap")
sgp_datas, sgp_binaries, sgp_hidden = collect_all("sgp4")

a = Analysis(
    ["ftdx1200_app.py"],
    pathex=[],
    binaries=webview_binaries + sd_binaries + np_binaries + voa_binaries + sgp_binaries,
    datas=[
        ("ftdx1200/static", "static"),
        ("ftdx1200/rtl", "rtl"),
        *webview_datas,
        *sd_datas,
        *np_datas,
        *voa_datas,
        *sgp_datas,
    ],
    hiddenimports=[
        "serial",
        "serial.tools.list_ports",
        "flask",
        "jinja2",
        "werkzeug",
        "werkzeug.serving",
        "click",
        "itsdangerous",
        "markupsafe",
        "webview",
        "webview.platforms.winforms",
        "webview.platforms.edgechromium",
        "pythonnet",
        "clr",
        "sounddevice",
        "numpy",
        "dvoacap",
        "sgp4",
        "sgp4.api",
        "ftdx1200.iss",
        *webview_hidden,
        *sd_hidden,
        *np_hidden,
        *voa_hidden,
        *sgp_hidden,
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "PIL", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Shackboard",
    icon="ftdx1200/static/ftdx1200.ico",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
