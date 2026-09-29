Vendored static assets (no Node build step):
- bootstrap 5.3.8 (MIT)
- bootstrap-icons 1.11.4 (MIT)
- htmx.org 2.0.11 (0BSD)
Update by replacing files; keep versions in this note.
Strip "sourceMappingURL" comments when updating: .map files are not vendored
and ManifestStaticFilesStorage fails collectstatic on missing references.
- fonts: Unbounded, Onest, JetBrains Mono (SIL OFL 1.1), variable woff2, cyrillic+latin; used by the /help/kontur/ page
