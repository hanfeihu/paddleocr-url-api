# PyInstaller spec for Windows x64 build.
# Build command:
#   pyinstaller --noconfirm --clean windows/pyinstaller.spec

import os
from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)


block_cipher = None

hiddenimports = []
datas = []
binaries = []

# Paddle / PaddleOCR / PaddleX need hidden imports plus package data and
# native runtime libraries (configs, fonts, bundled DLLs, etc).
for package_name in ('paddle', 'paddleocr', 'paddlex'):
    hiddenimports += collect_submodules(package_name)
    datas += collect_data_files(package_name)
    binaries += collect_dynamic_libs(package_name)

# uvicorn/fastapi stack
hiddenimports += collect_submodules('uvicorn')
hiddenimports += collect_submodules('fastapi')
hiddenimports += collect_submodules('starlette')

# PyInstaller may set SPECPATH to a relative path or just the basename.
spec_dir = os.path.abspath(os.path.dirname(SPECPATH) or os.getcwd())

# Find repository root (this spec lives under <root>/windows/pyinstaller.spec).
if os.path.isdir(os.path.join(spec_dir, 'windows')) and os.path.isfile(os.path.join(spec_dir, 'windows', 'main.py')):
    repo_root = spec_dir
elif os.path.basename(spec_dir).lower() == 'windows' and os.path.isfile(os.path.join(spec_dir, 'main.py')):
    repo_root = os.path.abspath(os.path.join(spec_dir, os.pardir))
else:
    repo_root = spec_dir

windows_dir = os.path.join(repo_root, 'windows')
models_dir = os.path.join(windows_dir, 'models')

# Include offline models.
# Layout: windows/models/official_models/...
if os.path.isdir(os.path.join(models_dir, 'official_models')):
    datas.append((models_dir, 'models'))
else:
    raise SystemExit('Missing offline models at windows/models/official_models')


a = Analysis(
    [os.path.join(windows_dir, 'main.py')],
    pathex=[repo_root, windows_dir],
    binaries=binaries,
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
    upx=False,
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
    upx=False,
    upx_exclude=[],
    name='ocr-url-api',
)
