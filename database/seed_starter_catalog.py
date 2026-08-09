#!/usr/bin/env python3
"""Seed a starter Chic A Boo catalog through the live admin API.

Everything it creates is ordinary content the admin panel can edit or delete —
it exists so the storefront has something real to render before the owners add
their own products. Idempotent: rows are matched by slug and skipped if present.

Usage:
    .venv/bin/python database/seed_starter_catalog.py [--base-url http://localhost:8000]
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import httpx

ADMIN_EMAIL = "admin@chicaboo.com"
ADMIN_PASSWORD = "Chicaboo@Admin2026"

# Covers already shipped in the storefront's public/collections folder.
TULIPS = "/collections/tulips.jpeg"
HIBISCUS = "/collections/hibiscus-flowers.jpeg"
CROCHET_BOUQUET = "/collections/tulip-crochet-bouque.jpeg"
TRAVEL = "/collections/customised-travelling.jpeg"
POLAROID = "/collections/polaroid-picture-box.jpeg"
FLOWER_POT = "/collections/crochet-flower-pot.jpeg"
KEYCHAINS = "/collections/key-chains.jpeg"

SECTIONS: list[dict] = [
    {
        "name": "Crochet Bouquets",
        "description": "Hand-crocheted blooms that never wilt — our signature gift.",
        "image_r2_key": CROCHET_BOUQUET,
        "categories": [
            {
                "name": "Single Stem Bouquets",
                "description": "One perfect bloom, wrapped and ribboned.",
                "image_r2_key": TULIPS,
                "products": [
                    ("Blush Tulip Single Stem", "A single crochet tulip in blush pink, wrapped in ivory paper.", 449, 599, [TULIPS, CROCHET_BOUQUET]),
                    ("Scarlet Rose Stem", "One deep-red crochet rose with a satin ribbon tie.", 499, None, [CROCHET_BOUQUET, TULIPS]),
                ],
            },
            {
                "name": "Bunch Bouquets",
                "description": "Full bouquets of five to fifteen handmade stems.",
                "image_r2_key": CROCHET_BOUQUET,
                "products": [
                    ("Pastel Dream Bouquet", "Nine crochet stems in soft pastels with kraft wrap.", 1499, 1899, [CROCHET_BOUQUET, TULIPS, HIBISCUS]),
                    ("Sunset Hibiscus Bunch", "Seven hibiscus blooms in warm sunset tones.", 1299, None, [HIBISCUS, CROCHET_BOUQUET]),
                    ("Jumbo Ivory Bouquet", "Fifteen ivory-and-gold stems — our largest arrangement.", 2499, 2999, [TULIPS, CROCHET_BOUQUET]),
                ],
            },
        ],
    },
    {
        "name": "Keepsakes & Frames",
        "description": "Little things that hold a memory — pots, frames and charms.",
        "image_r2_key": FLOWER_POT,
        "categories": [
            {
                "name": "Crochet Flower Pots",
                "description": "Potted crochet arrangements for a desk or shelf.",
                "image_r2_key": FLOWER_POT,
                "products": [
                    ("Mini Desk Bloom Pot", "A palm-sized pot with three crochet blooms.", 699, 849, [FLOWER_POT]),
                    ("Terracotta Daisy Pot", "Five crochet daisies in a hand-painted pot.", 899, None, [FLOWER_POT, HIBISCUS]),
                ],
            },
            {
                "name": "Keychains & Charms",
                "description": "Pocket-sized crochet charms, perfect as party favours.",
                "image_r2_key": KEYCHAINS,
                "products": [
                    ("Crochet Flower Keychain", "A tiny crochet bloom on a gold-tone ring.", 249, 349, [KEYCHAINS]),
                    ("Initial Charm Set", "Set of three lettered crochet charms.", 399, None, [KEYCHAINS]),
                ],
            },
        ],
    },
    {
        "name": "Personalised Gifts",
        "description": "Made for one person in particular — photos, books and hampers.",
        "image_r2_key": POLAROID,
        "categories": [
            {
                "name": "Polaroid Picture Boxes",
                "description": "Your photographs, boxed and ribboned.",
                "image_r2_key": POLAROID,
                "products": [
                    ("Polaroid Memory Box · 20", "Twenty printed polaroids in a keepsake box.", 999, 1249, [POLAROID]),
                    ("Polaroid Memory Box · 50", "Fifty polaroids with a hand-lettered lid.", 1799, None, [POLAROID, TRAVEL]),
                ],
            },
            {
                "name": "Customised Hampers",
                "description": "Build-your-own gift hampers for every occasion.",
                "image_r2_key": TRAVEL,
                "products": [
                    ("Travel Lover's Hamper", "A curated hamper for the one always packing a bag.", 2299, 2799, [TRAVEL, POLAROID]),
                    ("Anniversary Keepsake Hamper", "Bouquet, photo box and a hand-written note.", 2999, None, [TRAVEL, CROCHET_BOUQUET, POLAROID]),
                ],
            },
        ],
    },
]

TESTIMONIALS: list[dict] = [
    {
        "author_name": "Ananya S.",
        "author_role": "Delhi · Pastel Dream Bouquet",
        "quote": "I ordered for my sister's birthday and she genuinely thought they were real. Three months on they still look brand new on her desk.",
        "rating": 5,
        "is_featured": True,
    },
    {
        "author_name": "Rhea M.",
        "author_role": "Mumbai · Polaroid Memory Box",
        "quote": "The packaging alone made me tear up. Every photo was printed perfectly and the box feels like something you keep forever.",
        "rating": 5,
        "is_featured": True,
    },
    {
        "author_name": "Kavya & Arjun",
        "author_role": "Bengaluru · Anniversary Hamper",
        "quote": "We asked for a last-minute anniversary hamper and they still managed a handwritten note. That's the kind of care you can't fake.",
        "rating": 5,
    },
    {
        "author_name": "Nidhi P.",
        "author_role": "Pune · Crochet Flower Keychain",
        "quote": "Bought twelve as return gifts for a baby shower. Everyone asked where they were from.",
        "rating": 5,
    },
    {
        "author_name": "Sneha R.",
        "author_role": "Hyderabad · Sunset Hibiscus Bunch",
        "quote": "Delivery was two days earlier than promised and the colours are richer in person than on screen.",
        "rating": 5,
    },
    {
        "author_name": "Meera K.",
        "author_role": "Jaipur · Mini Desk Bloom Pot",
        "quote": "My whole team ordered one after seeing mine. It's the nicest thing on my desk.",
        "rating": 4,
    },
]


class Seeder:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client
        self.created = {"sections": 0, "categories": 0, "products": 0, "testimonials": 0}
        self.skipped = 0

    async def login(self) -> None:
        response = await self._client.post(
            "/admin/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        response.raise_for_status()
        token = response.json()["access_token"]
        self._client.headers["Authorization"] = f"Bearer {token}"

    async def existing_category_slugs(self) -> dict[str, str]:
        response = await self._client.get("/admin/categories")
        response.raise_for_status()

        out: dict[str, str] = {}

        def walk(nodes: list[dict]) -> None:
            for node in nodes:
                out[node["slug"]] = node["id"]
                walk(node.get("children") or [])

        walk(response.json())
        return out

    async def existing_product_slugs(self) -> set[str]:
        response = await self._client.get("/admin/products?page=1&page_size=100")
        response.raise_for_status()
        return {item["slug"] for item in response.json().get("items", [])}

    async def run(self) -> None:
        await self.login()
        categories = await self.existing_category_slugs()
        products = await self.existing_product_slugs()

        for section in SECTIONS:
            section_id = await self._ensure_category(
                categories,
                name=section["name"],
                description=section["description"],
                image_r2_key=section["image_r2_key"],
                kind="section",
                parent_id=None,
                counter="sections",
            )

            for category in section["categories"]:
                category_id = await self._ensure_category(
                    categories,
                    name=category["name"],
                    description=category["description"],
                    image_r2_key=category["image_r2_key"],
                    kind="category",
                    parent_id=section_id,
                    counter="categories",
                )

                for index, (name, blurb, price, compare, gallery) in enumerate(category["products"]):
                    await self._ensure_product(
                        products,
                        name=name,
                        blurb=blurb,
                        price=price,
                        compare=compare,
                        gallery=gallery,
                        category_id=category_id,
                        featured=index == 0,
                    )

        await self._seed_testimonials()

    async def _ensure_category(
        self,
        known: dict[str, str],
        *,
        name: str,
        description: str,
        image_r2_key: str,
        kind: str,
        parent_id: str | None,
        counter: str,
    ) -> str:
        slug = _slugify(name)
        if slug in known:
            self.skipped += 1
            return known[slug]

        response = await self._client.post(
            "/admin/categories",
            json={
                "name": name,
                "kind": kind,
                "parent_id": parent_id,
                "description": description,
                "image_r2_key": image_r2_key,
                "status": "active",
            },
        )
        response.raise_for_status()
        created = response.json()
        known[created["slug"]] = created["id"]
        self.created[counter] += 1
        print(f"  + {kind}: {name}")
        return created["id"]

    async def _ensure_product(
        self,
        known: set[str],
        *,
        name: str,
        blurb: str,
        price: int,
        compare: int | None,
        gallery: list[str],
        category_id: str,
        featured: bool,
    ) -> None:
        slug = _slugify(name)
        if slug in known:
            self.skipped += 1
            return

        response = await self._client.post(
            "/admin/products",
            json={
                "name": name,
                "primary_category_id": category_id,
                "short_description": blurb,
                "description": blurb,
                "status": "active",
                "is_featured": featured,
                "image_url": gallery[0],
                "gallery": gallery,
                "variant": {
                    "title": "Default",
                    "price_paise": price * 100,
                    "compare_at_price_paise": compare * 100 if compare else None,
                },
            },
        )
        response.raise_for_status()
        known.add(slug)
        self.created["products"] += 1
        print(f"    + product: {name} (Rs {price})")

    async def _seed_testimonials(self) -> None:
        response = await self._client.get("/admin/testimonials")
        response.raise_for_status()
        existing = {item["author_name"] for item in response.json().get("items", [])}

        for index, testimonial in enumerate(TESTIMONIALS):
            if testimonial["author_name"] in existing:
                self.skipped += 1
                continue
            created = await self._client.post(
                "/admin/testimonials", json={**testimonial, "sort_order": index}
            )
            created.raise_for_status()
            self.created["testimonials"] += 1
            print(f"  + testimonial: {testimonial['author_name']}")


def _slugify(value: str) -> str:
    import re

    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return re.sub(r"-{2,}", "-", slug)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    args = parser.parse_args()

    async with httpx.AsyncClient(base_url=args.base_url.rstrip("/"), timeout=60) as client:
        seeder = Seeder(client)
        try:
            await seeder.run()
        except httpx.HTTPStatusError as exc:
            print(f"FAILED: {exc.response.status_code} {exc.response.text[:300]}", file=sys.stderr)
            return 1

    summary = ", ".join(f"{count} {name}" for name, count in seeder.created.items())
    print(f"\nSeeded {summary} ({seeder.skipped} already existed).")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
