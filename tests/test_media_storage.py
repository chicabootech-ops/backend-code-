from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qs, quote, urlsplit
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.admin_api.schemas.bouquet import BouquetOptionCreate, BouquetOptionUpdate
from app.admin_api.schemas.category import CategoryCreate, CategoryUpdate
from app.admin_api.schemas.product import ProductCreate, ProductUpdate
from app.admin_api.schemas.testimonial import TestimonialCreate as QuoteCreate, TestimonialUpdate as QuoteUpdate
from app.admin_api.services import media_service
from app.admin_api.services.category_service import CategoryService
from app.storefront.lib import media
from app.storefront.services.catalog_service import category_to_out
from app.storefront.services.category_service import _to_out

ENDPOINT = 'https://test-account.r2.cloudflarestorage.com'
KEY = 'categories/flower space+rose%25.jpg'


def signed_url(key=KEY):
    return f'{ENDPOINT}/test-bucket/{quote(key)}?X-Amz-Date=20260826T142453Z&X-Amz-Expires=604800&X-Amz-Signature=expired'


@pytest.fixture(autouse=True)
def storage_settings(monkeypatch):
    monkeypatch.setattr(media, 'settings', SimpleNamespace(
        r2_endpoint=ENDPOINT,
        effective_r2_bucket_name='test-bucket',
        r2_public_base_url='',
        r2_configured=True,
        effective_r2_access_key_id='test-key',
        effective_r2_secret_access_key='test-secret',
    ))


@pytest.mark.parametrize('value,expected', [
    (None, None), ('', ''), (KEY, KEY),
    ('/collections/tulips.jpeg', '/collections/tulips.jpeg'),
    ('https://example.com/photo.jpg', 'https://example.com/photo.jpg'),
    (signed_url(), KEY),
    (signed_url().replace('test-account.', 'test-bucket.test-account.').replace('/test-bucket/', '/'), KEY),
])
def test_normalize_storage_key(value, expected):
    assert media.normalize_storage_key(value) == expected


def test_custom_public_url_becomes_key(monkeypatch):
    monkeypatch.setattr(media.settings, 'r2_public_base_url', 'https://images.example.com/media')
    assert media.normalize_storage_key('https://images.example.com/media/products/a%20b.jpg') == 'products/a b.jpg'
    assert 'X-Amz-Signature' in media.resolve_storage_url('products/a b.jpg')


@pytest.mark.parametrize('value', [
    signed_url().replace('/test-bucket/', '/another-bucket/'),
    signed_url().replace('test-account.', 'unknown-account.'),
    'https://example.com/photo?X-Amz-Signature=expired',
    f'{ENDPOINT}/test-bucket/a%ZZ.jpg?X-Amz-Signature=expired',
    f'{ENDPOINT}/test-bucket/%2Fa.jpg?X-Amz-Signature=expired',
    signed_url() + '#fragment',
])
def test_unsafe_urls_are_rejected(value):
    with pytest.raises(ValueError):
        media.normalize_storage_key(value)


@pytest.mark.parametrize('value', [KEY, signed_url()])
def test_response_is_fresh_presigned_url(value):
    result = media.resolve_storage_url(value)
    url = urlsplit(result)
    query = parse_qs(url.query)
    assert url.path == '/test-bucket/' + quote(KEY)
    assert query['X-Amz-Expires'] == ['3600']
    assert query['X-Amz-Signature'] != ['expired']
    signed_at = datetime.strptime(query['X-Amz-Date'][0], '%Y%m%dT%H%M%SZ').replace(tzinfo=UTC)
    assert abs((datetime.now(UTC) - signed_at).total_seconds()) < 5


def test_static_paths_and_empty_images_keep_contract():
    assert media.resolve_storage_url('/collections/tulips.jpeg') == '/collections/tulips.jpeg'
    assert media.resolve_storage_url('/collections/premium-blooms.jpg') is None
    assert media.resolve_storage_url(None) is None


@pytest.mark.parametrize('model,field,kwargs', [
    (CategoryCreate, 'image_r2_key', {'name': 'Flowers'}),
    (CategoryUpdate, 'image_r2_key', {}),
    (BouquetOptionCreate, 'image_r2_key', {'kind': 'flower', 'name': 'Rose'}),
    (BouquetOptionUpdate, 'image_r2_key', {}),
    (QuoteCreate, 'avatar_r2_key', {'author_name': 'A', 'quote': 'Great'}),
    (QuoteUpdate, 'avatar_r2_key', {}),
    (ProductCreate, 'image_url', {'name': 'Rose', 'primary_category_id': uuid4()}),
    (ProductUpdate, 'image_url', {}),
])
def test_write_inputs_store_keys(model, field, kwargs):
    payload = model(**kwargs, **{field: signed_url()})
    assert getattr(payload, field) == KEY
    with pytest.raises(ValidationError):
        model(**kwargs, **{field: 'https://unknown.example/a?X-Amz-Signature=expired'})


@pytest.mark.parametrize('model,kwargs', [
    (ProductCreate, {'name': 'Rose', 'primary_category_id': uuid4()}),
    (ProductUpdate, {}),
])
def test_product_metadata_and_galleries_normalized(model, kwargs):
    original = {'image_url': signed_url(), 'image_r2_key': signed_url(),
                'gallery': [signed_url(), '/collections/tulips.jpeg'],
                'images': [signed_url()], 'unrelated': {'text': 'keep'}}
    result = model(**kwargs, metadata=original, gallery=[signed_url()])
    assert result.gallery == [KEY]
    assert result.metadata['image_url'] == KEY
    assert result.metadata['image_r2_key'] == KEY
    assert result.metadata['gallery'] == [KEY, '/collections/tulips.jpeg']
    assert result.metadata['images'] == [KEY]
    assert result.metadata['unrelated'] == original['unrelated']
    assert original['image_url'] == signed_url()


def category():
    return SimpleNamespace(id=uuid4(), name='Flowers', slug='flowers', parent_id=None,
                           description=None, image_r2_key=KEY, sort_order=0, kind='section',
                           status='active', path=None, depth=0, metadata_={},
                           created_at=datetime.now(UTC), updated_at=datetime.now(UTC))


@pytest.mark.parametrize('serialize', [category_to_out, _to_out])
def test_category_response_preserves_key_and_serves_signed_url(serialize):
    row = category()
    out = serialize(row)
    assert out.image_url.startswith(ENDPOINT)
    assert 'X-Amz-Signature=' in out.image_url
    assert row.image_r2_key == KEY


async def test_category_create_update_persist_key():
    row = category()
    service = CategoryService(AsyncMock())
    service._repo = SimpleNamespace(slug_exists=AsyncMock(return_value=False),
                                   create=AsyncMock(return_value=row),
                                   get_by_id=AsyncMock(return_value=row),
                                   update=AsyncMock(return_value=row))
    service._audit = SimpleNamespace(log=AsyncMock())
    await service.create(CategoryCreate(name='Flowers', image_r2_key=signed_url()), admin_id=uuid4())
    assert service._repo.create.call_args.args[0]['image_r2_key'] == KEY
    await service.update(row.id, CategoryUpdate(image_r2_key=signed_url()), admin_id=uuid4())
    assert service._repo.update.call_args.args[1]['image_r2_key'] == KEY


def test_upload_returns_key_separately_from_preview(monkeypatch):
    put = Mock(return_value=True)
    monkeypatch.setattr(media_service, 'put_bytes', put)
    result = media_service.upload_image(data=b'GIF89a' + b'\0' * 20, filename='flower.gif')
    assert result.key.startswith('categories/')
    assert '?' not in result.key
    assert put.call_args.args[0] == result.key
    assert 'X-Amz-Signature=' in result.url
    payload = CategoryCreate(name='Flowers', image_r2_key=result.url)
    assert payload.image_r2_key == result.key


async def test_product_create_update_persist_only_keys():
    from app.admin_api.services.product_service import ProductService

    row = SimpleNamespace(id=uuid4(), name='Rose', slug='rose', metadata_={})
    service = ProductService(AsyncMock())
    service._repo = SimpleNamespace(
        slug_exists=AsyncMock(return_value=False),
        create_product=AsyncMock(return_value=row),
        create_variant=AsyncMock(return_value=SimpleNamespace()),
        get_by_id=AsyncMock(return_value=row),
        update_product=AsyncMock(return_value=row),
        get_variants=AsyncMock(return_value=[]),
    )
    service._categories = SimpleNamespace(get_by_id=AsyncMock(
        return_value=SimpleNamespace(kind='category', parent_id=uuid4())))
    service._audit = SimpleNamespace(log=AsyncMock())
    service._to_out = Mock()
    image_fields = {'image_url': signed_url(), 'gallery': [signed_url()],
                    'metadata': {'images': [signed_url()], 'image_r2_key': signed_url()}}
    await service.create(ProductCreate(name='Rose', primary_category_id=uuid4(), **image_fields),
                         admin_id=uuid4())
    stored = service._repo.create_product.call_args.args[0]['metadata_']
    assert stored == {'image_url': KEY, 'image_r2_key': KEY, 'gallery': [KEY], 'images': [KEY]}
    await service.update(row.id, ProductUpdate(**image_fields), admin_id=uuid4())
    assert service._repo.update_product.call_args.args[1]['metadata'] == stored
