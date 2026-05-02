# zotero-bridge
This script is designed to be read-only and local. It targets a specific Zotero Collection by name and outputs a JSON object that includes the metadata and absolute file paths for the PDFs.

## Usage

```bash
python zotero_bridge.py "QM / decay"
```

By default, this writes to a sanitized collection-name file such as `qm_decay_bib_data.json`.

```bash
python zotero_bridge.py "QM / decay" --output decay_refs.json
python zotero_bridge.py "QM / decay" --stdout
```

The script opens `~/Zotero/zotero.sqlite` with SQLite's `mode=ro&immutable=1` URI options and never writes to the database. Collection names can be provided as simple names when unique, or as full paths such as `QM / decay` when needed for disambiguation.

Each item includes its Zotero Better BibTeX/native citation key under the JSON key `Citation Key`, plus title, abstract, authors, item metadata, and PDF paths.
