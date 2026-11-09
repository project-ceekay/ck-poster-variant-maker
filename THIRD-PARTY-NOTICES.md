# Third-party notices

CK Poster Variant Maker's own code is MIT-licensed (see `LICENSE`). The installer also
contains the following components, each under its own license:

| Component | License | Source |
|---|---|---|
| Python runtime | Python Software Foundation License | https://www.python.org |
| Tcl/Tk (the window toolkit) | Tcl/Tk License (BSD-style) | https://www.tcl.tk |
| Pillow (image processing) | MIT-CMU (HPND) License | https://github.com/python-pillow/Pillow |
| Little CMS (colour management, inside Pillow) | MIT License | https://www.littlecms.com |
| libjpeg-turbo, libpng, zlib, libwebp, libtiff, FreeType (inside Pillow) | Their respective permissive licenses (IJG/BSD, libpng, zlib, BSD, libtiff, FreeType) | see Pillow's wheel licenses |
| PyInstaller bootloader | GPL-2.0 with the PyInstaller bootloader exception, which allows bundling programs under any license | https://github.com/pyinstaller/pyinstaller |

The full license texts of the bundled Python packages are included in the installed
program's `_internal` folder (`*.dist-info` folders).
