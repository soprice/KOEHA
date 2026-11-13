# Third-party notices for the Windows bundle

KOEHA's own source is distributed under the MIT license. The Windows installer
and portable ZIP also contain independent third-party libraries under their
respective terms. Additional notices supplied by installed packages are in
`licenses/wheels` and under `_internal`.

## Qt and Qt for Python

KOEHA uses Qt and Qt for Python (PySide6 Essentials and Shiboken6), copyright
The Qt Company Ltd. and other contributors, under the GNU LGPL version 3.
The bundle ships Qt and its Python bindings as separate DLLs and extension
modules under `_internal`. The application may be used with compatible modified
versions of these libraries, and you may reverse engineer the application to
debug your modifications to the libraries. Close KOEHA before replacing the
libraries in a copy of the portable bundle.

The GNU LGPL and GPL license texts are in `licenses`. The corresponding Qt and
Qt for Python source archives, including the upstream notices and build files,
are available at no charge in `KOEHA-Third-Party-Source.zip` beside the Windows
downloads on the [same GitHub Release](https://github.com/soprice/KOEHA/releases).
See the included `release-sources.json` for the source versions and SHA-256 hashes.

## PyAV and FFmpeg

KOEHA uses PyAV (BSD 3-Clause) and FFmpeg (copyright the FFmpeg developers)
from the PyAV Windows wheel. The upstream FFmpeg build reports LGPL version 3 or later
and ships separately linked third-party libraries, including x264 and x265.
The source ZIP contains the PyAV and FFmpeg sources, the matching vendor build
recipes and patches, and the source archives listed in `release-sources.json`.
GNU license texts for the FFmpeg components are included in `licenses`.
The LAME fork listed as `lamer` in the vendor recipe was renamed to `bwlame`.
The source archive uses the commit for the same upstream `v3.101.0` tag.

## Other components

The Windows bundle includes Python and uses NumPy, ONNX Runtime, PortAudio
through PyAudioWPatch, and uv. Their terms apply to their components. Model
weights and separately downloaded recognition runtimes are covered by the
[model license notes](https://github.com/soprice/KOEHA/blob/main/docs/MODEL_LICENSES.md).
