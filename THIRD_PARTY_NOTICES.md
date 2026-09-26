# Third-party components

The reserved-rights notice for Professor Compressor's original code does not
apply to third-party components. Their respective licenses remain in effect.
The application does not modify the installed FFmpeg npm distributions.

## Browser components

| Package | Pinned version | Declared license |
| --- | --- | --- |
| `@ffmpeg/ffmpeg` | 0.12.15 | MIT |
| `@ffmpeg/util` | 0.12.2 | MIT |
| `@ffmpeg/core` | 0.12.10 | GPL-2.0-or-later |
| `@ffmpeg/core-mt` | 0.12.10 | GPL-2.0-or-later |

Copyright (c) 2019 Jerome Wu applies to the MIT wrapper. Its full license is
included in [the browser notices](professor_compressor/static/third-party-notices.txt).
The core includes FFmpeg and external libraries with their own copyright notices
and licenses; the wrapper's MIT license must not be substituted for those terms.
The GPL version 2 text is included in
[COPYING.GPLv2](professor_compressor/static/COPYING.GPLv2).

- [Upstream ffmpeg.wasm source](https://github.com/ffmpegwasm/ffmpeg.wasm)
- [Upstream license explanation](https://ffmpegwasm.netlify.app/docs/faq/)
- [FFmpeg licensing](https://ffmpeg.org/legal.html)
- [Core 0.12.10 build recipe](https://github.com/ffmpegwasm/ffmpeg.wasm/blob/63ea4ccb6c58e127cc1767f88c508371936178c2/Dockerfile)
- [Core build scripts and bindings](https://github.com/ffmpegwasm/ffmpeg.wasm/tree/63ea4ccb6c58e127cc1767f88c508371936178c2)

The core-mt npm release identifies the above revision as its `gitHead`; that
revision also declares core 0.12.10. It is a source reference, not proof of a
bit-for-bit rebuild of both published npm artifacts.

The recipe builds FFmpeg n5.1.4 with x264, x265, libvpx, LAME, Ogg, Theora,
Opus, Vorbis, zlib, WebP, FreeType, FriBidi, HarfBuzz, libass, and zimg, using
Emscripten 3.1.40. Its source URLs and build options are recorded in the recipe.
Some dependencies use moving branches, so the exact source revisions used for
the distributed binaries must be confirmed before claiming complete
corresponding-source availability.

## Distribution requirements

Serving WebAssembly to browsers distributes those components. Preserve their
license texts and copyright notices, and provide the corresponding source and
build materials required by their licenses. A link to an upstream repository
alone is not a substitute for verifying those requirements. The notices in this
repository are not a certification that a deployment or redistributed image
satisfies every applicable license obligation.

Do not publish a binary release or container image without verifying its full
dependency notices and corresponding-source provisions. The original-code
reuse policy is a separate decision and does not override third-party rights.

## Server and development dependencies

Python packages, the Python and Node container bases, Caddy, and development
tools retain their respective upstream licenses. Package metadata and bundled
license files must be retained when distributing them. `requirements.txt`,
`requirements-dev.txt`, `package-lock.json`, `Dockerfile`, and `compose.yaml`
identify the dependencies; the browser table is not a complete container SBOM.
