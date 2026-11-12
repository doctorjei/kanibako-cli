# Portable YAML conversion

Adopted from the donated Yapyon converter, kept together to preserve its value verification and comment-association algorithm. It imports no Kanibako code and performs no file I/O.

`from_yaml` accepts a selected safe YAML loader; application policy and owned-output normalization belong to the caller. Alias identity and source layout are not preserved. Notes report alias expansion and merge-comment relocation.

This helper has no runtime or command wiring. Normalization tests select `yaml_compatible=True`, then call public `yamlplus.normalize` and the owned reader.
