# Third-party notices

LineageBox is copyright © 2026 Scott Gusler and licensed under the GNU Affero General Public License, version 3
(`AGPL-3.0-only`), in [LICENSE](LICENSE), with the [additional permission](#additional-permission-for-openssl) below.

This file lists what LineageBox's repository and its Docker images include that others made, and under which licences.
Where a licence asks for an acknowledgement, it is given here. Both images carry this file and LICENSE in `/app`.

## Additional permission for OpenSSL

Finding faces loads OpenCV (InsightFace uses it), and the OpenCV build LineageBox installs carries OpenSSL 1.1, whose
licence can't be combined with the AGPL on its own terms. This permission allows it:

> **Additional permission under GNU AGPL version 3 section 7**
>
> If you modify this Program, or any covered work, by linking or combining it with OpenSSL (or a modified version of
> that library), containing parts covered by the terms of the OpenSSL or SSLeay licenses, the licensors of this
> Program grant you additional permission to convey the resulting work. Corresponding Source for a non-source form of
> such a combination shall include the source code for the parts of OpenSSL used as well as that of the covered work.

## In this repository

| Component | Where | Licence |
|---|---|---|
| [Leaflet](https://leafletjs.com) 1.9.4, the map | `lineagebox/web/static/vendor/leaflet-1.9.4/` | BSD-2-Clause. © 2010-2023 Volodymyr Agafonkin, © 2010-2011 CloudMade. The text is in `LICENSE` next to it. |
| [GeoNames](https://www.geonames.org/) towns and cities, for naming photos with GPS after the nearest town | `lineagebox/data/towns.tsv.gz` | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), © GeoNames. Changed: `cities5000.txt` (populated places of over 5,000 people, downloaded 2026-11-07) trimmed to each town's name, position (rounded to 3 decimals), country and region (names from `countryInfo.txt` and `admin1CodesASCII.txt`) and population in thousands. The Map page credits it. |

Translators' tool, not in the repository or the images: [Babel](https://babel.pocoo.org)'s `pybabel` (BSD-3-Clause),
a development dependency (`pip install -e ".[dev]"`) that extracts the text to translate and compiles catalogs
([translating](docs/translating.md)). LineageBox doesn't need it to run; it reads compiled catalogs with Python's own
`gettext`.

## In the LineageBox image

`ghcr.io/mutilator/lineagebox`, built from `Dockerfile`.

**The base:** `python:3.12-slim`, Docker's official Python image on **Debian 13 (trixie)**. Python itself is under the
PSF License (`/usr/local/lib/python3.12/LICENSE.txt`).

**Debian packages**, installed from Debian unchanged, with the libraries they need:

- **FFmpeg** (`ffmpeg` and its `libav*` libraries), as Debian builds it: with GPL parts enabled and linked to x264 and
  x265, so as built it is under the **GPL-2.0-or-later**.
- **ExifTool** (`libimage-exiftool-perl`), © Phil Harvey, and **Perl**: the terms of Perl itself (GPL-1.0-or-later or
  the Artistic License).
- **tzdata**: public domain.
- **DejaVu Sans** (`fonts-dejavu-core`, which ffmpeg's libraries bring in anyway), for the words drawn on tours and
  albums saved as a video: the Bitstream Vera Fonts licence and public domain (changes by the DejaVu authors), which
  allow using and passing on the fonts with any software.

Each Debian package's copyright and licence is in `/usr/share/doc/<package>/copyright`. `/app/packages.txt` lists
every Debian package in the image and its exact version.

**Python packages**, in `/venv`. `docker/requirements.txt` pins the ones LineageBox asks for; `/app/python-packages.txt`
lists the exact set. Each package's licence files are in its `*.dist-info` folder in
`/venv/lib/python3.12/site-packages`.

| Licence | Packages |
|---|---|
| MIT | annotated-doc, annotated-types, anyio, cbor2, charset-normalizer, fastapi, h11, insightface (the library; its models are [below](#downloaded-when-lineagebox-runs)), narwhals, onnxruntime, pip, pydantic, pydantic-core, rawpy, typing-inspection, urllib3 |
| BSD-3-Clause | click, cloudpickle, httpcore, httpx, idna, Jinja2, joblib, lazy-loader, MarkupSafe, networkx, numpy (some parts 0BSD, MIT, Zlib or CC0-1.0), pillow-heif (its own code; the libraries in its wheel are listed below), protobuf, pycparser, scikit-image (some parts BSD-2-Clause), scikit-learn, scipy, segno, starlette, threadpoolctl, tifffile, uvicorn, webauthn |
| MIT-0 | cffi |
| MIT and BSD-3-Clause | PyWavelets |
| BSD-2-Clause | ImageHash, imageio, pyasn1, pyasn1-modules |
| Apache-2.0 | flatbuffers, ml_dtypes, onnx, opencv-python-headless, opentelemetry-api, pyOpenSSL, requests |
| Apache-2.0 or BSD-2-Clause | packaging |
| Apache-2.0 or BSD-3-Clause | cryptography |
| MPL-2.0 | certifi; tqdm (MPL-2.0 and MIT) |
| PSF-2.0 | typing_extensions |
| MIT-CMU | Pillow |

**Native libraries inside those packages.** Some wheels carry compiled libraries of their own, in `<package>.libs/`
or inside the package:

- **opencv-python-headless:** OpenCV, plus FFmpeg's libavcodec, libavformat, libavutil, libswresample and libswscale
  (**LGPL-2.1-or-later**), libvpx (BSD-3-Clause), libaom and libavif (BSD-2-Clause), libpng, and OpenSSL 1.1's
  libcrypto and libssl (OpenSSL License). Their texts are in `cv2/LICENSE-3RD-PARTY.txt`. It also carries OpenBLAS
  and the GCC Fortran runtime (as numpy does, below) and libdrm (MIT, the text in `/usr/share/doc/libdrm2/copyright`).
- **rawpy:** LibRaw, compiled in (LGPL-2.1 or CDDL-1.0; rawpy ships it under the **LGPL-2.1**, in
  `rawpy-*.dist-info/licenses/LICENSE.LibRaw`), with JasPer (JasPer License 2.0), libjpeg-turbo, Little CMS (MIT) and
  GCC's OpenMP runtime (GPL-3.0-or-later with the GCC Runtime Library Exception).
- **pillow-heif** (HEIC photos): libheif 1.23.6 and libde265 1.1.3 (**LGPL-3.0**), and the x265 4.3 encoder
  (**GPL-2.0-or-later**), which pillow-heif says makes its wheel as a whole GPL-licensed. Their versions and the
  links to their source are in `pillow_heif-*.dist-info/licenses/LICENSES_bundled.txt`; the texts of the GPL and LGPL
  are in `/usr/share/common-licenses`.
- **Pillow:** FreeType (FreeType License), HarfBuzz, libjpeg-turbo (IJG, BSD-3-Clause and Zlib licences), Little CMS,
  libwebp, libtiff, OpenJPEG, libpng, libavif, Brotli, liblzma, zstd, libxcb, libXau and zlib. Their texts are in
  `pillow-*.dist-info/licenses/LICENSE`.
- **numpy and scipy:** OpenBLAS with LAPACK (BSD-3-Clause), the GCC Fortran runtime (GPL-3.0-or-later with the GCC
  Runtime Library Exception) and libquadmath (**LGPL-2.1-or-later**). Their texts are in each one's `LICENSE.txt`.
- **scikit-learn:** GCC's OpenMP runtime (GPL-3.0-or-later with the GCC Runtime Library Exception).
- **cryptography** (for passkeys): OpenSSL 4 (Apache-2.0) compiled in, and the Rust crates it is built from (most MIT
  or Apache-2.0). Their texts are in `cryptography-*.dist-info/licenses/`.
- **onnxruntime and ml_dtypes:** code compiled in from other projects, among them Eigen (MPL-2.0), listed with their
  texts in `onnxruntime/ThirdPartyNotices.txt` and `ml_dtypes-*.dist-info/licenses/`.

**Acknowledgements these libraries ask for:**

- Portions of this software are copyright © The FreeType Project (https://freetype.org). All rights reserved.
- This software is based in part on the work of the Independent JPEG Group.

## In the whisper image

`ghcr.io/mutilator/lineagebox-whisper`, built from `whisper_service/Dockerfile`, for transcripts of videos.

- **The base:** the same `python:3.12-slim` (Debian 13), with Debian's `libgomp1` (GCC's OpenMP runtime,
  GPL-3.0-or-later with the GCC Runtime Library Exception). `/opt/whisper/packages.txt` lists every Debian package and
  its version.
- **[whisper.cpp](https://github.com/ggml-org/whisper.cpp) v1.9.5 and ggml**, built from source: MIT, © The ggml
  authors. Compiled into its two programs (`whisper-server`, `whisper-vad-speech-segments`) along with:
  - [cpp-httplib](https://github.com/yhirose/cpp-httplib) 0.20.0 (whisper-server): MIT, © Yuji Hirose;
  - [JSON for Modern C++](https://github.com/nlohmann/json) 3.11.2 (whisper-server): MIT, © 2013-2022 Niels Lohmann;
  - [miniaudio](https://miniaud.io) 0.11.24, for reading the sound: public domain (Unlicense) or MIT-0, © David Reid;
  - [stb_vorbis](https://github.com/nothings/stb) 1.22: public domain (Unlicense) or MIT, © Sean Barrett;
  - ggml's YaRN code: MIT, © 2023 Jeffrey Quesnelle and Bowen Peng.

  Their licence texts are in `/opt/whisper/licenses`.
- **Python packages:** fastapi, uvicorn, httpx and the packages they need, under the licences in the table above
  (mostly MIT and BSD-3-Clause; opentelemetry-api Apache-2.0, certifi MPL-2.0, typing_extensions PSF-2.0).
  `/opt/whisper/python-packages.txt` lists them; their licence files are in `/usr/local/lib/python3.12/site-packages`.

## Source code for copyleft components

The images include, unchanged, software under the GPL and LGPL: FFmpeg with x264 and x265 and other Debian packages,
FFmpeg's libraries inside OpenCV, LibRaw inside rawpy, libheif, libde265 and x265 inside pillow-heif, libquadmath and
the GCC runtime libraries.

- **Debian packages:** the source of every version in `packages.txt` is on
  [snapshot.debian.org](https://snapshot.debian.org), which keeps every version Debian has published.
- **Python packages:** their source releases are on [PyPI](https://pypi.org), next to the wheels. The libraries in
  pillow-heif's wheels are built from the upstream versions its `LICENSES_bundled.txt` links to.
- **Written offer:** for three years after we last distribute an image, we will give anyone who asks the complete
  corresponding source code of the GPL and LGPL software in it, on a medium customarily used for software interchange,
  for no more than the cost of making and sending it. Ask at scott@g-tech.dev, naming the image's tag or
  digest.

## Downloaded when LineageBox runs

These are not part of LineageBox or its images. LineageBox or the services it uses download them the first time they are
needed, and their own terms apply. Each file LineageBox and the whisper service download is a fixed version, used only
when its SHA-256 is the one in the code (`lineagebox/faces.py`, `whisper_service/checksums.py`).

- **InsightFace `buffalo_l` face models**, into the catalog folder. InsightFace makes its models available **for
  non-commercial research purposes only**. Check that your use fits, or turn faces off on the Settings page. For other
  licensing: recognition-oss-pack@insightface.ai.
- **Whisper models**, by the whisper service from [Hugging Face](https://huggingface.co/ggerganov/whisper.cpp):
  OpenAI's Whisper, converted for whisper.cpp. MIT, © 2022 OpenAI.
- **Silero VAD**, the model that finds the speech, by the whisper service from
  [Hugging Face](https://huggingface.co/ggml-org/whisper-vad). MIT, © Silero Team.
- **The Ollama model you choose**, by Ollama (a separate program, MIT). The default `ministral-3` is under the
  Apache-2.0 licence. `gemma3` is under the [Gemma Terms of Use](https://ai.google.dev/gemma/terms) and its
  [Prohibited Use Policy](https://ai.google.dev/gemma/prohibited_use_policy). Each model's page on
  [ollama.com](https://ollama.com/library) names its licence. The default model for search by meaning,
  `nomic-embed-text` (Nomic AI), is under the Apache-2.0 licence; like the others, Ollama downloads it when asked,
  and LineageBox doesn't include it.

## Online services

- **The map and place lookups:** map data © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright),
  under the Open Database License (ODbL). The map's tiles come from tile.openstreetmap.org and, when finding places is
  on, place names are looked up at nominatim.openstreetmap.org. Both are used under the OpenStreetMap Foundation's
  [Tile Usage Policy](https://operations.osmfoundation.org/policies/tiles/) and
  [Nominatim Usage Policy](https://operations.osmfoundation.org/policies/nominatim/). Both servers are settings.
- **Sign in with Google**, with family accounts on: used under Google's
  [API Terms of Service](https://developers.google.com/terms) and
  [API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy). Google and the
  Google logo are trademarks of Google LLC.
- **Looking for updates**, once a day unless switched off: the newest release of LineageBox's repository is asked of GitHub's
  REST API (api.github.com), under GitHub's [Terms of Service](https://docs.github.com/site-policy/github-terms/github-terms-of-service).
- **QR Code** is a registered trademark of DENSO WAVE INCORPORATED.
