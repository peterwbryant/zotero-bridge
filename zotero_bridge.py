#!/usr/bin/env python3
import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path


ZOTERO_DATA_DIR = Path("~/Zotero").expanduser()
ZOTERO_DB = ZOTERO_DATA_DIR / "zotero.sqlite"
STORAGE_DIR = ZOTERO_DATA_DIR / "storage"


def connect_read_only(db_path):
    uri = f"file:{db_path}?mode=ro&immutable=1"
    return sqlite3.connect(uri, uri=True)


def fetch_field_ids(cur, field_names):
    placeholders = ",".join("?" for _ in field_names)
    cur.execute(
        f"""
        SELECT fieldName, fieldID
        FROM fields
        WHERE fieldName IN ({placeholders})
        """,
        tuple(field_names),
    )
    return dict(cur.fetchall())


def fetch_collection(cur, collection_name):
    cur.execute(
        """
        WITH RECURSIVE collection_paths AS (
            SELECT
                collectionID,
                libraryID,
                key,
                collectionName,
                parentCollectionID,
                collectionName AS path
            FROM collections
            WHERE parentCollectionID IS NULL

            UNION ALL

            SELECT
                c.collectionID,
                c.libraryID,
                c.key,
                c.collectionName,
                c.parentCollectionID,
                collection_paths.path || ' / ' || c.collectionName AS path
            FROM collections c
            JOIN collection_paths ON c.parentCollectionID = collection_paths.collectionID
        )
        SELECT collectionID, libraryID, key, collectionName, path
        FROM collection_paths
        WHERE collectionName = ? OR path = ?
        ORDER BY libraryID, path
        """,
        (collection_name, collection_name),
    )
    matches = cur.fetchall()

    if not matches:
        raise ValueError(f"No Zotero collection found for {collection_name!r}")
    if len(matches) > 1:
        paths = "\n".join(f"- {row[4]} (libraryID={row[1]}, key={row[2]})" for row in matches)
        raise ValueError(
            f"Collection name {collection_name!r} is ambiguous. Use the full path instead:\n{paths}"
        )

    return matches[0]


def item_field_subquery(field_id):
    if field_id is None:
        return "NULL"
    return (
        "(SELECT idv.value FROM itemData id "
        "JOIN itemDataValues idv USING (valueID) "
        f"WHERE id.itemID = items.itemID AND id.fieldID = {int(field_id)})"
    )


def resolve_attachment_path(raw_path, attachment_key):
    if not raw_path:
        return None

    if raw_path.startswith("storage:"):
        filename = raw_path.removeprefix("storage:")
        return str(STORAGE_DIR / attachment_key / filename)

    return os.path.expanduser(raw_path)


def default_output_path(collection_path):
    stem = re.sub(r"[^A-Za-z0-9]+", "_", collection_path).strip("_").lower()
    if not stem:
        stem = "zotero_collection"
    return Path(f"{stem}_bib_data.json")


def fetch_authors(cur, item_id):
    cur.execute(
        """
        SELECT c.firstName, c.lastName, c.fieldMode
        FROM creators c
        JOIN itemCreators ic USING (creatorID)
        JOIN creatorTypes ct USING (creatorTypeID)
        WHERE ic.itemID = ? AND ct.creatorType = 'author'
        ORDER BY ic.orderIndex
        """,
        (item_id,),
    )

    authors = []
    for first_name, last_name, field_mode in cur.fetchall():
        if field_mode == 1:
            authors.append(last_name or "")
        else:
            authors.append(" ".join(part for part in (first_name, last_name) if part))
    return authors


def fetch_pdf_attachments(cur, item_id):
    cur.execute(
        """
        SELECT ia.path, attachment_items.key
        FROM itemAttachments ia
        JOIN items attachment_items ON attachment_items.itemID = ia.itemID
        WHERE ia.parentItemID = ?
          AND ia.contentType = 'application/pdf'
        ORDER BY attachment_items.dateAdded, attachment_items.itemID
        """,
        (item_id,),
    )

    attachments = []
    for raw_path, attachment_key in cur.fetchall():
        path = resolve_attachment_path(raw_path, attachment_key)
        attachments.append(
            {
                "path": path,
                "filename": os.path.basename(path) if path else None,
                "attachment_key": attachment_key,
            }
        )
    return attachments


def fetch_collection_data(collection_name, db_path=ZOTERO_DB):
    with connect_read_only(db_path) as conn:
        cur = conn.cursor()
        field_ids = fetch_field_ids(cur, ("title", "citationKey", "abstractNote"))
        collection_id, library_id, collection_key, resolved_name, collection_path = fetch_collection(
            cur, collection_name
        )

        title_expr = item_field_subquery(field_ids.get("title"))
        citekey_expr = item_field_subquery(field_ids.get("citationKey"))
        abstract_expr = item_field_subquery(field_ids.get("abstractNote"))

        cur.execute(
            f"""
            SELECT
                items.itemID,
                items.key AS item_key,
                it.typeName AS item_type,
                {title_expr} AS title,
                {citekey_expr} AS citation_key,
                {abstract_expr} AS abstract
            FROM items
            JOIN itemTypesCombined it USING (itemTypeID)
            JOIN collectionItems ci USING (itemID)
            WHERE ci.collectionID = ?
              AND items.itemID NOT IN (SELECT itemID FROM itemAttachments)
              AND items.itemID NOT IN (SELECT itemID FROM itemNotes)
            ORDER BY ci.orderIndex, title COLLATE NOCASE
            """,
            (collection_id,),
        )
        rows = cur.fetchall()

        items = []
        for item_id, item_key, item_type, title, citation_key, abstract in rows:
            attachments = fetch_pdf_attachments(cur, item_id)
            items.append(
                {
                    "Citation Key": citation_key,
                    "title": title,
                    "abstract": abstract,
                    "authors": fetch_authors(cur, item_id),
                    "item_key": item_key,
                    "item_type": item_type,
                    "pdfs": attachments,
                    "path": attachments[0]["path"] if attachments else None,
                }
            )

        return {
            "collection": {
                "name": resolved_name,
                "path": collection_path,
                "libraryID": library_id,
                "collectionID": collection_id,
                "key": collection_key,
            },
            "items": items,
        }


def parse_args():
    parser = argparse.ArgumentParser(
        description="Export metadata and PDF paths for a Zotero collection as JSON."
    )
    parser.add_argument("collection_name", help="Collection name or full path, e.g. 'QM / decay3k'")
    parser.add_argument(
        "--db",
        default=str(ZOTERO_DB),
        help=f"Path to zotero.sqlite (default: {ZOTERO_DB})",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Output JSON path. Defaults to '<collection>_bib_data.json'.",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print JSON to stdout instead of writing a file.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        data = fetch_collection_data(args.collection_name, Path(args.db).expanduser())
    except (sqlite3.Error, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    output = json.dumps(data, indent=2, ensure_ascii=False)
    if args.stdout:
        print(output)
        return 0

    output_path = Path(args.output).expanduser() if args.output else default_output_path(
        data["collection"]["path"]
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(output + "\n", encoding="utf-8")
    print(f"Wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
