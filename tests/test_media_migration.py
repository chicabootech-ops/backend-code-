import os
from pathlib import Path

import psycopg2
import pytest
from psycopg2.extras import Json

MIGRATION = Path(__file__).parents[1] / 'database/migrations/000045_normalize_r2_image_keys.sql'
ENDPOINT = 'https://test-account.r2.cloudflarestorage.com'


@pytest.fixture
def connection():
    url = os.environ.get('R2_MIGRATION_TEST_DATABASE_URL')
    if not url:
        pytest.skip('R2_MIGRATION_TEST_DATABASE_URL is required for PostgreSQL migration tests')
    conn = psycopg2.connect(url, connect_timeout=15)
    try:
        with conn.cursor() as cur:
            cur.execute('CREATE TEMP TABLE categories (id int, image_r2_key text)')
            cur.execute('CREATE TEMP TABLE products (id int, metadata jsonb)')
            cur.execute('CREATE TEMP TABLE bouquet_options (image_r2_key text)')
            cur.execute('CREATE TEMP TABLE testimonials (avatar_r2_key text)')
            cur.execute("SELECT set_config('chicaboo.r2_endpoint', %s, true)", (ENDPOINT,))
            cur.execute("SELECT set_config('chicaboo.r2_bucket', 'test-bucket', true)")
            cur.execute("SELECT set_config('chicaboo.r2_public_base', 'https://images.example.com/media', true)")
        yield conn
    finally:
        conn.rollback()
        conn.close()


def migrate(cur):
    cur.execute(MIGRATION.read_text().replace('commerce.', 'pg_temp.'))


def test_migration_normalizes_and_is_idempotent(connection):
    urls = [
        f'{ENDPOINT}/test-bucket/categories/a%20b%2Bc%2520.jpg?X-Amz-Signature=expired',
        'https://test-bucket.test-account.r2.cloudflarestorage.com/products/rose.jpg?X-Amz-Signature=expired',
        'https://images.example.com/media/products/%E2%9C%BF.jpg',
        '/collections/tulips.jpeg',
        'categories/already-key.jpg',
        'https://external.example.com/photo.jpg',
        None,
    ]
    expected = ['categories/a b+c%20.jpg', 'products/rose.jpg', 'products/✿.jpg', *urls[3:]]
    with connection.cursor() as cur:
        for i, url in enumerate(urls):
            cur.execute('INSERT INTO pg_temp.categories VALUES (%s, %s)', (i, url))
        metadata = {'image_url': urls[0], 'image_r2_key': urls[1], 'gallery': urls[:2],
                    'images': [], 'unrelated': {'keep': [1, 2]}}
        cur.execute('INSERT INTO pg_temp.products VALUES (1, %s)', (Json(metadata),))
        cur.execute('INSERT INTO pg_temp.bouquet_options VALUES (%s)', (urls[0],))
        cur.execute('INSERT INTO pg_temp.testimonials VALUES (%s)', (urls[1],))
        for _ in range(2):
            migrate(cur)
            cur.execute('SELECT image_r2_key FROM pg_temp.categories ORDER BY id')
            assert [r[0] for r in cur.fetchall()] == expected
            cur.execute('SELECT metadata FROM pg_temp.products')
            result = cur.fetchone()[0]
            assert result == {**metadata, 'image_url': expected[0], 'image_r2_key': expected[1], 'gallery': expected[:2]}
            cur.execute('SELECT image_r2_key FROM pg_temp.bouquet_options')
            assert cur.fetchone()[0] == expected[0]
            cur.execute('SELECT avatar_r2_key FROM pg_temp.testimonials')
            assert cur.fetchone()[0] == expected[1]


@pytest.mark.parametrize('url', [
    f'{ENDPOINT}/wrong-bucket/categories/a.jpg?X-Amz-Signature=expired',
    'https://unknown.example.com/a.jpg?X-Amz-Signature=expired',
    f'{ENDPOINT}/test-bucket/a%ZZ.jpg?X-Amz-Signature=expired',
    f'{ENDPOINT}/test-bucket/%2Fa.jpg?X-Amz-Signature=expired',
])
def test_migration_rejects_ambiguous_records_without_partial_writes(connection, url):
    valid = f'{ENDPOINT}/test-bucket/categories/valid.jpg?X-Amz-Signature=expired'
    with connection.cursor() as cur:
        cur.execute('INSERT INTO pg_temp.categories VALUES (1, %s)', (valid,))
        cur.execute('INSERT INTO pg_temp.products VALUES (1, %s)', (Json({'image_url': url}),))
        cur.execute('SAVEPOINT migration_test')
        with pytest.raises(psycopg2.Error):
            migrate(cur)
        cur.execute('ROLLBACK TO SAVEPOINT migration_test')
        cur.execute('SELECT image_r2_key FROM pg_temp.categories')
        assert cur.fetchone()[0] == valid
