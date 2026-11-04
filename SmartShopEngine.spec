# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['src/bundle/entry_main.py'],
    pathex=['.'],
    binaries=[],
    datas=[('src/frontend', 'src/frontend')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['torch', 'transformers', 'sentence_transformers', 'accelerate', 'tokenizers', 'PyQt5', 'PySide6', 'sip', 'qtpy', 'matplotlib', 'IPython', 'ipython', 'bokeh', 'plotly', 'dask', 'xarray', 'tables', 'numexpr', 'sklearn', 'scipy', 'seaborn', 'notebook', 'jupyter', 'statsmodels', 'gevent', 'pycurl', 'pyarrow'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SmartShopEngine',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='SmartShopEngine',
)
