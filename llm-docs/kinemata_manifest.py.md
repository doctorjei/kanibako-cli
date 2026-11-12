# ManifestRegistry

Keep this tooling module outside the shipped package. Editable installs expose `src/`
for Kinemata's public import loader; product code must not import it.

The loader forwards `where` to the constructor before applying selection itself.
Accept it without filtering or forwarding it to MappingRegistry. The loader also
sets the name. Preserve base matching defaults and complete rows; KeyspaceRegistry's
parametric membership is unsuitable for these finite views.

`manifest_doc()` reads installed resources. Contract tests require adapter, package,
and resource attribution to the same checkout; mixed installs are not equivalent.
