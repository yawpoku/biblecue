# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all

# ndi-python ships a compiled extension plus the NDI runtime as binary
# payload (not plain Python) — collect_all pulls all three (submodules,
# data, binaries) instead of just the hiddenimport, which is what a
# package like this needs to actually work once frozen. Optional: if
# ndi-python isn't installed in the build environment, the NDI output
# simply stays unavailable (same graceful fallback as OBS at runtime),
# so this doesn't need to hard-fail the build.
try:
    _ndi_datas, _ndi_binaries, _ndi_hidden = collect_all('NDIlib')
except Exception:
    _ndi_datas, _ndi_binaries, _ndi_hidden = [], [], []

a = Analysis(
    ['biblecue.py'],
    pathex=[],
    binaries=[*_ndi_binaries],
    datas=[('output_plugins.py', '.'), *_ndi_datas],
    hiddenimports=['output_plugins', 'scriptures', 'websockets', 'sounddevice', 'numpy', 'scipy', 'PIL', 'obswebsocket', *_ndi_hidden],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='backend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
