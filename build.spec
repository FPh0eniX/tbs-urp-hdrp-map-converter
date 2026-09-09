# Сборка одного exe без консоли. Запускать через build.bat.
from PyInstaller.utils.hooks import collect_data_files

datas = collect_data_files("tkinterdnd2")  # библиотеки перетаскивания tkdnd

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=["tkinterdnd2"],
    excludes=[
        "matplotlib",
        "scipy",
        "pandas",
        "pytest",
        "IPython",
        "numpy.f2py",
        "PIL.ImageQt",
        "PyQt5",
        "PySide6",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name="URP Map Converter",
    console=False,
    upx=False,
    strip=False,
    exclude_binaries=False,
)
