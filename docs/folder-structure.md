# Folder structure

Episodes are saved as:

```
<output>/
└── One Pace/
    ├── Season 01/       <- Romance Dawn
    │   ├── .lang        <- "es-subs", "en-subs" or "es-dub" (replacement marker)
    │   ├── *[Es Dub]*   <- second version, only for the episodes that have a dub
    │   └── *.mp4
    ├── Season 02/       <- Orange Town
    └── ...
```

Each arc maps to a season number based on its position on onepace.net. Season numbers are stable: arcs keep their number even if earlier arcs are missing.
