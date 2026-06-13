# Host images

Dev-host container images, consumed elsewhere in the kanibako tooling/dev
workflow. These are **not** part of the `kanibako-*` base matrix
(min/oci/lxc/vm built from `../containers/Containerfile.kanibako`) and are not
built by `build-images.yml` or `release.yml`.

- `Containerfile.host` — base dev-host image.
- `Containerfile.host-claude` — dev-host image with the Claude agent layered on.
