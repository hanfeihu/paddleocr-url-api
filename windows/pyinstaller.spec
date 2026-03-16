# PyInstaller spec for Windows x64 build.
# Build command:
#   pyinstaller --noconfirm --clean windows/pyinstaller.spec

import os
from PyInstaller.utils.hooks import collect_submodules


block_cipher = None

hiddenimports = []

# Paddle / PaddleOCR have dynamic imports.
hiddenimports += collect_submodules('paddle')
hiddenimports += collect_submodules('paddleocr')
hiddenimports += collect_submodules('paddlex')

# uvicorn/fastapi stack
hiddenimports += collect_submodules('uvicorn')
hiddenimports += collect_submodules('fastapi')
hiddenimports += collect_submodules('starlette')


datas = []

# Include offline models.
# Layout: windows/models/official_models/...
if os.path.isdir(os.path.join('windows', 'models', 'official_models')):
    datas.append((os.path.join('windows', 'models'), 'models'))
else:
    raise SystemExit('Missing offline models at windows/models/official_models')


a = Analysis(
    ['windows/main.py'],
    pathex=['.'],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ocr-url-api',
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
    name='ocr-url-api',
)
