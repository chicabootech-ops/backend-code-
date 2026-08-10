-- Chic A Boo product prices captured 2026-08-10T12:44:01
-- Restores every variant price to its value before the flat ₹1 test pricing.
-- Run with:  psql "$DATABASE_URL" -f restore-prices-20260810-124359.sql
BEGIN;
UPDATE commerce.product_variants SET price_paise = 299900, compare_at_price_paise = NULL WHERE id = '88f4a23d-6b69-410a-aba1-f0164344e41c';  -- Anniversary Keepsake Hamper
UPDATE commerce.product_variants SET price_paise = 44900, compare_at_price_paise = 59900 WHERE id = '206027ee-f0db-4b30-8973-410a400be233';  -- Blush Tulip Single Stem
UPDATE commerce.product_variants SET price_paise = 29900, compare_at_price_paise = NULL WHERE id = '953fbc82-b681-4492-ad86-1d47e53307bd';  -- Build Your Own Bouquet
UPDATE commerce.product_variants SET price_paise = 24900, compare_at_price_paise = 34900 WHERE id = 'e97d0d37-8ed4-4592-9854-64bc490dcf92';  -- Crochet Flower Keychain
UPDATE commerce.product_variants SET price_paise = 39900, compare_at_price_paise = NULL WHERE id = '81d806cb-f6cd-440d-9c4e-dee9ef9c78a9';  -- Initial Charm Set
UPDATE commerce.product_variants SET price_paise = 249900, compare_at_price_paise = 299900 WHERE id = 'd14094f2-94cd-4397-b7d0-0a0f7ba8ded9';  -- Jumbo Ivory Bouquet
UPDATE commerce.product_variants SET price_paise = 69900, compare_at_price_paise = 84900 WHERE id = '29226732-df48-46cd-97a4-2593dca18f6c';  -- Mini Desk Bloom Pot
UPDATE commerce.product_variants SET price_paise = 149900, compare_at_price_paise = 189900 WHERE id = 'b9c7359d-7aac-48be-834c-e458e29c5dc2';  -- Pastel Dream Bouquet
UPDATE commerce.product_variants SET price_paise = 99900, compare_at_price_paise = 124900 WHERE id = '9b2e6707-d826-4884-9999-adbc82f8e30e';  -- Polaroid Memory Box · 20
UPDATE commerce.product_variants SET price_paise = 179900, compare_at_price_paise = NULL WHERE id = '5b119fde-01a1-45c4-8633-0ba8d1856417';  -- Polaroid Memory Box · 50
UPDATE commerce.product_variants SET price_paise = 49900, compare_at_price_paise = NULL WHERE id = 'a5d88eea-0154-4c70-a15a-1597bd5bc267';  -- Scarlet Rose Stem
UPDATE commerce.product_variants SET price_paise = 149900, compare_at_price_paise = 199900 WHERE id = '6bd675c5-a8ba-43e0-a8d0-10e7e1bb4fcd';  -- Smoke Tulip Box
UPDATE commerce.product_variants SET price_paise = 129900, compare_at_price_paise = NULL WHERE id = '56f55798-5430-4fe4-b488-4d328aac2b8c';  -- Sunset Hibiscus Bunch
UPDATE commerce.product_variants SET price_paise = 89900, compare_at_price_paise = NULL WHERE id = '9380ef14-98b1-4b3b-8015-cfdd68ead558';  -- Terracotta Daisy Pot
UPDATE commerce.product_variants SET price_paise = 229900, compare_at_price_paise = 279900 WHERE id = '84e47d30-9963-45ee-a199-a3d058f0cccf';  -- Travel Lover's Hamper
COMMIT;
