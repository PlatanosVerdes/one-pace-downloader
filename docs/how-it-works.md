# How it works

1. Scrapes `onepace.net/es/watch` (Spanish) first.
2. For any arc not yet available in Spanish, falls back to `onepace.net/en/watch` automatically. Affected arcs are logged and tagged `[EN]` in the output.
3. After each download, writes a `.lang` marker per season (`es-subs`, `en-subs`, `es-dub`). On subsequent runs a season is deleted and re-downloaded when either its marker or its actual files stop matching what the preferences ask for. The marker catches an arc reaching the Spanish page, or one that has to stop using what it was taking before. The files catch what the marker cannot: a run that replaced half a season and died, or one that wrote the marker before an older file was removed. Files whose variant cannot be read from the name are left alone rather than replaced on every pass forever, and so are the dub files kept as a second version, which say nothing about whether the season itself is right. A season holding nothing but dubbed files still does.
