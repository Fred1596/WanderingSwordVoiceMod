# Third-party tools

This directory intentionally contains no binaries. Download tools from their
original projects and verify their licenses and checksums.

Expected local layout:

```text
tools/
  repak/repak.exe
  uassetgui/UAssetGUI.exe       # optional
  ueviewer/umodel.exe           # optional
vendor/
  ue4ss-runtime-extracted/      # required only for community package builds
```

`catalog/extract_localization.py` accepts `--repak`, so the executable may be
stored elsewhere. `catalog/export_review_portraits.py` accepts `--umodel`.

Relevant upstream projects:

- repak: <https://github.com/trumank/repak>
- UE4SS: <https://github.com/UE4SS-RE/RE-UE4SS>
- UAssetGUI: <https://github.com/atenfyr/UAssetGUI>
- UE Viewer / umodel: <https://www.gildor.org/en/projects/umodel>

Do not commit downloaded executables. `.gitignore` excludes the paths above.
