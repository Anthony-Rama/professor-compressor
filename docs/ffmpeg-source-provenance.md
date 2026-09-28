# FFmpeg source verification

Status: **incomplete**. This checklist tracks the source provenance of the
browser binaries; passing application tests does not complete it.

The app serves the unmodified `@ffmpeg/core` and `@ffmpeg/core-mt` 0.12.10 npm
packages. The repository includes notices, license texts, and upstream build
references in [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md). Some source
dependencies in that recipe refer to moving branches. We have not established
the exact revisions used to build both published artifacts.

## Completion checklist

1. Record the npm package integrity values from `package-lock.json` and hashes
   of the JavaScript and WebAssembly files actually served by the release.
2. Obtain an upstream source manifest for those exact artifacts, including the
   FFmpeg and external-library revisions, patches, Emscripten/toolchain version,
   configuration flags, and build scripts. A matching npm version or `gitHead`
   alone does not establish all of these inputs.
3. Archive the matching source and build materials at a stable, publicly
   accessible location and connect the browser notices to that material.
   Confirm that the archive covers both single-thread and multithread builds.
4. If the original build inputs cannot be established, create a separately
   versioned build from fully pinned, archived inputs instead. Do not silently
   replace files under an existing immutable asset URL. Run the media and
   browser regression suites against the replacement before deploying it.
5. Review the source-distribution arrangement and notices against the included
   component licenses, with qualified licensing advice where needed. Record
   the release artifact hashes, source location, and verification date here.

Do not mark this complete based only on upstream links or successful video
conversion. Serving the browser binaries is already distribution; this is not
only a concern for a future downloadable release or container registry.

The project's original-code reuse policy is separate from the permissions and
requirements attached to these third-party components.
