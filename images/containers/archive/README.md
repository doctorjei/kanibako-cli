# Archived Containerfiles

Retired, experimental language/toolchain images. They are **not** part of the
`kanibako-*` base matrix (min/oci/lxc/vm built from `../Containerfile.kanibako`)
and are not built or published by CI. Kept for reference only.

- `Containerfile.base` — early standalone base image (Ubuntu devel + core
  packages), predecessor of the droste-based variants.
- `Containerfile.behemoth` — kitchen-sink image layering extra archive/utility
  tools (p7zip, etc.) on top of the base.
- `Containerfile.systems` — systems-programming toolchain image, not part of the
  kanibako-* base matrix.
- `Containerfile.jvm` — Java/JVM toolchain image (OpenJDK + build tools).
- `Containerfile.dotnet` — C#/.NET SDK toolchain image.
- `Containerfile.android` — Android build toolchain image (Gradle), built on the
  jvm image.
- `Containerfile.ndk` — Android NDK toolchain image, built on the android image.
