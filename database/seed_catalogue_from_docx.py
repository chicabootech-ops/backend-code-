#!/usr/bin/env python3
"""Wipe the live catalog and seed products from `without rates.docx`.

Every product is priced at ₹300. Images are uploaded to R2 from the extracted
docx media folder. Soft-deletes existing products/categories so order history
(RESTRICT on order_items) stays intact while the storefront goes empty, then
re-seeds a fresh tree.

Usage:
    .venv/bin/python database/seed_catalogue_from_docx.py \\
        [--media /tmp/chicaboo-catalogue] [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import uuid
from pathlib import Path

import asyncpg
import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

ROOT = Path(__file__).resolve().parents[1]
PRICE_PAISE = 300 * 100
BRAND = "Chic A Boo"

# (name, short_description, image filename under --media)
PRODUCTS_BY_CATEGORY: dict[str, list[tuple[str, str, str]]] = {
    "wrapped-bouquets": [
        (
            "Pastel Tulip Wrap",
            "Hand-crocheted pastel tulips wrapped in kraft paper with a satin bow.",
            "image21.jpeg",
        ),
        (
            "Daisy Wrap",
            "Maroon daisy blooms with cream tulips in a soft paper wrap.",
            "image24.jpeg",
        ),
        (
            "Lily Bliss",
            "A delicate crochet lily bouquet wrapped for gifting.",
            "image25.jpeg",
        ),
        (
            "Pastel Wrap",
            "Soft pastel crochet stems in a classic gift wrap.",
            "image26.jpeg",
        ),
        (
            "Sunny Tulip",
            "Bright sunny tulips crocheted by hand and ribbon-tied.",
            "image28.jpeg",
        ),
        (
            "Rose Wrap",
            "Classic crochet roses arranged in a gift wrap.",
            "image29.jpeg",
        ),
        (
            "Ocean Daisy Wrap",
            "Ocean-toned daisy wrap — cool blues and soft whites.",
            "image30.jpeg",
        ),
    ],
    "potted-blooms": [
        (
            "Crimson Rose Pot",
            "Three crimson crochet roses in a textured white planter.",
            "image38.jpeg",
        ),
        (
            "Blush Tulip Pot",
            "Blush tulips potted for a desk or shelf.",
            "image39.jpeg",
        ),
        (
            "Blue Moon Vase",
            "Blue moon crochet blooms in a vase arrangement.",
            "image40.jpeg",
        ),
        (
            "Rustic Rose Pot",
            "Rustic rose pot with warm earthy tones.",
            "image41.jpeg",
        ),
        (
            "Blue Blossom Pot",
            "Blue blossom crochet pot — cute, colourful, made with care.",
            "image42.jpeg",
        ),
        (
            "Frosted Daisy Pot",
            "Frosted daisy pot perfect for gifting and décor.",
            "image43.jpeg",
        ),
        (
            "Heart Pot",
            "A heartfelt crochet bloom in a mini pot.",
            "image56.jpeg",
        ),
        (
            "Lily Pot",
            "Crochet lily in a handmade pot.",
            "image58.jpeg",
        ),
        (
            "Rose Pot",
            "Single crochet rose in a woven-style pot.",
            "image59.jpeg",
        ),
        (
            "Sunflower Pot",
            "Sunny crochet sunflower in a mini pot.",
            "image60.jpeg",
        ),
        (
            "Daisy Pot",
            "Cheerful crochet daisy pot for little moments of joy.",
            "image55.jpeg",
        ),
    ],
    "charms": [
        ("Evil Eye Keychain", "Crochet evil-eye charm on a silver key ring.", "image69.jpeg"),
        ("Guitar Keychain", "Tiny crochet guitar keychain — handmade and heartmade.", "image70.jpeg"),
        ("Strawberries Keychain", "Sweet crochet strawberry charm.", "image71.jpeg"),
        ("Sunflower Keychain", "Pocket-sized crochet sunflower charm.", "image72.jpeg"),
        ("Tulip Keychain", "Mini crochet tulip keychain.", "image73.jpeg"),
        ("Cherry Keychain", "Crochet cherry pair keychain.", "image75.jpeg"),
        ("Star Keychain", "Crochet star charm for bags and keys.", "image74.jpeg"),
        ("Red Heart Keychain", "Red heart crochet keychain.", "image76.jpeg"),
    ],
    "mini-pouches": [
        (
            "Multi Stripe Pouch",
            "Earth-tone striped crochet drawstring pouch.",
            "image78.jpeg",
        ),
        (
            "Colour Red Glow Pouch",
            "Vibrant red crochet pouch with a white flower accent.",
            "image79.jpeg",
        ),
        (
            "Termelo Watermelon Pouch",
            "Watermelon-slice crochet drawstring pouch.",
            "image81.jpeg",
        ),
        (
            "Baby Pink Pouch",
            "Baby pink crochet pouch with a soft drawstring.",
            "image83.jpeg",
        ),
        (
            "Shaded Pouch",
            "Soft shaded blue-and-white crochet drawstring pouch.",
            "image84.jpeg",
        ),
        (
            "Avacardo Pouch",
            "Avocado-motif crochet pouch — cute and handmade.",
            "image84.jpeg",
        ),
    ],
    "flower-galleries": [
        (
            "Rose Gallery",
            "Statement crochet rose from the Rose Gallery collection.",
            "image92.png",
        ),
        (
            "Tulip Gallery",
            "Hand-crocheted tulip blooms from the Tulip Gallery.",
            "image106.jpeg",
        ),
    ],
    "gift-boxes": [
        (
            "Glam Box",
            "Curated glam gift box from the Chic A Boo collection.",
            "image21.jpeg",
        ),
    ],
}

SECTIONS: list[dict] = [
    {
        "name": "Crochet Flower Wraps",
        "slug": "crochet-flower-wraps",
        "description": "Hand-wrapped crochet bouquets that never wilt.",
        "cover": "image21.jpeg",
        "categories": [
            {
                "name": "Wrapped Bouquets",
                "slug": "wrapped-bouquets",
                "description": "Gift-wrapped crochet flower bunches.",
                "cover": "image24.jpeg",
                "products_key": "wrapped-bouquets",
            }
        ],
    },
    {
        "name": "Crochet Flower Pots",
        "slug": "crochet-flower-pots-section",
        "description": "Potted crochet blooms for desks, shelves and gifting.",
        "cover": "image38.jpeg",
        "categories": [
            {
                "name": "Potted Blooms",
                "slug": "potted-blooms",
                "description": "Mini pots and vases of everlasting crochet flowers.",
                "cover": "image40.jpeg",
                "products_key": "potted-blooms",
            }
        ],
    },
    {
        "name": "Crochet Keychains",
        "slug": "crochet-keychains",
        "description": "Cute. Handmade. Heartmade.",
        "cover": "image69.jpeg",
        "categories": [
            {
                "name": "Charms",
                "slug": "charms",
                "description": "Pocket-sized crochet charms and keychains.",
                "cover": "image69.jpeg",
                "products_key": "charms",
            }
        ],
    },
    {
        "name": "Crochet Pouches",
        "slug": "crochet-pouches",
        "description": "Cute, stylish handmade drawstring pouches.",
        "cover": "image81.jpeg",
        "categories": [
            {
                "name": "Mini Pouches",
                "slug": "mini-pouches",
                "description": "Palm-sized crochet pouches and potlis.",
                "cover": "image78.jpeg",
                "products_key": "mini-pouches",
            }
        ],
    },
    {
        "name": "Galleries & Boxes",
        "slug": "galleries-boxes",
        "description": "Rose and tulip galleries, plus gift boxes.",
        "cover": "image92.png",
        "categories": [
            {
                "name": "Flower Galleries",
                "slug": "flower-galleries",
                "description": "Individual gallery blooms.",
                "cover": "image106.jpeg",
                "products_key": "flower-galleries",
            },
            {
                "name": "Gift Boxes",
                "slug": "gift-boxes",
                "description": "Ready-to-gift glam boxes.",
                "cover": "image21.jpeg",
                "products_key": "gift-boxes",
            },
        ],
    },
]


def _load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for path in (ROOT / ".env", ROOT / "database" / ".env"):
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env.setdefault(key.strip(), value.strip().strip('"'))
    return env


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return re.sub(r"-{2,}", "-", slug)


def _content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".png":
        return "image/png"
    if suffix == ".webp":
        return "image/webp"
    return "application/octet-stream"


class R2:
    def __init__(self, env: dict[str, str]) -> None:
        account = env.get("R2_ACCOUNT_ID", "")
        endpoint = env.get("R2_ENDPOINT_URL") or (
            f"https://{account}.r2.cloudflarestorage.com" if account else ""
        )
        self.bucket = env.get("R2_BUCKET_NAME") or env.get("R2_BUCKET") or ""
        key = env.get("R2_ACCESS_KEY_ID") or env.get("R2_ACCESS_KEY") or ""
        secret = env.get("R2_SECRET_ACCESS_KEY") or env.get("R2_SECRET_KEY") or ""
        if not (endpoint and self.bucket and key and secret):
            raise RuntimeError("R2 is not configured in .env")
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=key,
            aws_secret_access_key=secret,
            region_name="auto",
            config=Config(signature_version="s3v4"),
        )
        self._cache: dict[str, str] = {}

    def upload(self, folder: str, path: Path) -> str:
        cache_key = f"{folder}:{path.resolve()}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        data = path.read_bytes()
        if len(data) > 8 * 1024 * 1024:
            raise RuntimeError(f"{path.name} is larger than 8MB")
        key = f"{folder}/{uuid.uuid4().hex}{path.suffix.lower().replace('jpeg', 'jpg')}"
        if key.endswith(".jpeg"):
            key = key[:-5] + ".jpg"
        try:
            self._client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=data,
                ContentType=_content_type(path),
            )
        except (ClientError, BotoCoreError) as exc:
            raise RuntimeError(f"R2 upload failed for {path.name}: {exc}") from exc
        self._cache[cache_key] = key
        return key


async def wipe(conn: asyncpg.Connection) -> None:
    await conn.execute("DELETE FROM commerce.cart_items")
    await conn.execute(
        """
        UPDATE commerce.product_variants
           SET deleted_at = COALESCE(deleted_at, now()), status = 'inactive'
         WHERE deleted_at IS NULL
        """
    )
    await conn.execute(
        """
        UPDATE commerce.products
           SET deleted_at = COALESCE(deleted_at, now()), status = 'archived', updated_at = now()
         WHERE deleted_at IS NULL
        """
    )
    # Detach orphaned children whose parent is already soft-deleted — the tree
    # trigger refuses any update while parent_id points at a deleted row.
    orphans = await conn.fetch(
        """
        SELECT c.id, c.slug
          FROM commerce.categories c
          LEFT JOIN commerce.categories p ON p.id = c.parent_id AND p.deleted_at IS NULL
         WHERE c.deleted_at IS NULL
           AND c.parent_id IS NOT NULL
           AND p.id IS NULL
        """
    )
    for orphan in orphans:
        await conn.execute(
            """
            UPDATE commerce.categories
               SET parent_id = NULL,
                   depth = 0,
                   path = '/' || slug,
                   updated_at = now()
             WHERE id = $1
            """,
            orphan["id"],
        )
    await conn.execute(
        """
        UPDATE commerce.categories
           SET deleted_at = COALESCE(deleted_at, now()), status = 'inactive', updated_at = now()
         WHERE deleted_at IS NULL AND parent_id IS NOT NULL
        """
    )
    await conn.execute(
        """
        UPDATE commerce.categories
           SET deleted_at = COALESCE(deleted_at, now()), status = 'inactive', updated_at = now()
         WHERE deleted_at IS NULL AND parent_id IS NULL
        """
    )


async def insert_category(
    conn: asyncpg.Connection,
    *,
    name: str,
    slug: str,
    description: str,
    image_key: str | None,
    kind: str,
    parent_id: uuid.UUID | None,
    sort_order: int,
) -> uuid.UUID:
    depth = 0 if parent_id is None else 1
    path = f"/{slug}" if parent_id is None else f"/{slug}"
    row = await conn.fetchrow(
        """
        INSERT INTO commerce.categories (
            name, slug, parent_id, sort_order, description, image_r2_key,
            status, kind, path, depth, metadata
        )
        VALUES (
            $1, $2, $3, $4, $5, $6,
            'active', $7, $8, $9, '{}'::jsonb
        )
        RETURNING id
        """,
        name,
        slug,
        parent_id,
        sort_order,
        description,
        image_key,
        kind,
        path,
        depth,
    )
    assert row is not None
    cat_id = row["id"]
    if parent_id is not None:
        parent = await conn.fetchrow(
            "SELECT path FROM commerce.categories WHERE id = $1", parent_id
        )
        parent_path = (parent["path"] if parent else "") or ""
        await conn.execute(
            "UPDATE commerce.categories SET path = $1 WHERE id = $2",
            f"{parent_path.rstrip('/')}/{slug}",
            cat_id,
        )
    return cat_id


async def insert_product(
    conn: asyncpg.Connection,
    *,
    name: str,
    blurb: str,
    category_id: uuid.UUID,
    image_key: str,
    featured: bool,
) -> None:
    slug = _slugify(name)
    product_id = await conn.fetchval(
        """
        INSERT INTO commerce.products (
            primary_category_id, name, slug, description, short_description,
            brand, status, is_featured, metadata
        )
        VALUES (
            $1, $2, $3, $4, $5,
            $6, 'active', $7, $8::jsonb
        )
        RETURNING id
        """,
        category_id,
        name,
        slug,
        blurb,
        blurb,
        BRAND,
        featured,
        json.dumps({"image_url": image_key, "gallery": [image_key]}),
    )
    await conn.execute(
        """
        INSERT INTO commerce.product_variants (
            product_id, sku, title, option_values, price_paise, status
        )
        VALUES ($1, $2, 'Default', '{}'::jsonb, $3, 'active')
        """,
        product_id,
        f"{slug}-default",
        PRICE_PAISE,
    )
    await conn.execute(
        """
        INSERT INTO commerce.product_categories (product_id, category_id, is_primary)
        VALUES ($1, $2, TRUE)
        ON CONFLICT DO NOTHING
        """,
        product_id,
        category_id,
    )


async def run(*, media: Path, dry_run: bool) -> int:
    env = _load_env()
    db_url = (env.get("DATABASE_MIGRATE_URL") or env.get("DATABASE_URL") or "").replace(
        "postgresql+asyncpg://", "postgresql://"
    )
    if not db_url:
        print("DATABASE_URL missing", file=sys.stderr)
        return 1
    if not media.is_dir():
        print(f"Media folder not found: {media}", file=sys.stderr)
        return 1

    missing = []
    for items in PRODUCTS_BY_CATEGORY.values():
        for _, _, filename in items:
            if not (media / filename).exists():
                missing.append(filename)
    if missing:
        print("Missing media files:", ", ".join(sorted(set(missing))), file=sys.stderr)
        return 1

    if dry_run:
        total = sum(len(v) for v in PRODUCTS_BY_CATEGORY.values())
        print(f"Dry run OK — would wipe catalog and seed {total} products at ₹300.")
        return 0

    r2 = R2(env)
    conn = await asyncpg.connect(db_url, ssl="require")
    try:
        async with conn.transaction():
            print("Wiping active products and categories…")
            await wipe(conn)
            active = await conn.fetchval(
                "SELECT count(*) FROM commerce.products WHERE deleted_at IS NULL"
            )
            print(f"  active products now: {active}")

            created_products = 0
            for section_index, section in enumerate(SECTIONS):
                cover = r2.upload("categories", media / section["cover"])
                section_id = await insert_category(
                    conn,
                    name=section["name"],
                    slug=section["slug"],
                    description=section["description"],
                    image_key=cover,
                    kind="section",
                    parent_id=None,
                    sort_order=section_index,
                )
                print(f"+ section: {section['name']}")

                for cat_index, category in enumerate(section["categories"]):
                    cat_cover = r2.upload("categories", media / category["cover"])
                    category_id = await insert_category(
                        conn,
                        name=category["name"],
                        slug=category["slug"],
                        description=category["description"],
                        image_key=cat_cover,
                        kind="category",
                        parent_id=section_id,
                        sort_order=cat_index,
                    )
                    print(f"  + category: {category['name']}")

                    products = PRODUCTS_BY_CATEGORY[category["products_key"]]
                    for product_index, (name, blurb, filename) in enumerate(products):
                        image_key = r2.upload("products", media / filename)
                        await insert_product(
                            conn,
                            name=name,
                            blurb=blurb,
                            category_id=category_id,
                            image_key=image_key,
                            featured=product_index == 0,
                        )
                        created_products += 1
                        print(f"    + {name} (₹300)")

        products = await conn.fetchval(
            "SELECT count(*) FROM commerce.products WHERE deleted_at IS NULL"
        )
        categories = await conn.fetchval(
            "SELECT count(*) FROM commerce.categories WHERE deleted_at IS NULL"
        )
        print(f"\nDone. Active categories={categories}, products={products} (seeded {created_products}).")
    finally:
        await conn.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--media", default="/tmp/chicaboo-catalogue")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    return asyncio.run(run(media=Path(args.media), dry_run=args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
