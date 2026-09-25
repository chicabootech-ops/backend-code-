CREATE OR REPLACE FUNCTION pg_temp.r2_image_key(value TEXT)
RETURNS TEXT LANGUAGE plpgsql AS $$
DECLARE
    endpoint TEXT := rtrim(current_setting('chicaboo.r2_endpoint', true), '/');
    bucket TEXT := current_setting('chicaboo.r2_bucket', true);
    public_base TEXT := rtrim(current_setting('chicaboo.r2_public_base', true), '/');
    parts TEXT[];
    host TEXT;
    path TEXT;
    prefix TEXT;
    encoded TEXT;
    decoded BYTEA := ''::bytea;
    position INTEGER := 1;
BEGIN
    IF value IS NULL OR value !~* '^https?://' THEN
        RETURN value;
    END IF;
    parts := regexp_match(value, '^https?://([^/?#]+)(/[^?#]*)?(\?[^#]*)?(#.*)?$', 'i');
    host := lower(parts[1]);
    path := parts[2];
    IF endpoint IS NOT NULL AND endpoint <> '' AND bucket IS NOT NULL AND bucket <> '' THEN
        prefix := '/' || bucket || '/';
        IF host = lower(regexp_replace(endpoint, '^https?://', '', 'i'))
           AND starts_with(path, prefix) THEN
            encoded := substr(path, length(prefix) + 1);
        ELSIF host = lower(bucket || '.' || regexp_replace(endpoint, '^https?://', '', 'i')) THEN
            encoded := substr(path, 2);
        END IF;
    END IF;
    IF encoded IS NULL AND public_base IS NOT NULL AND public_base <> ''
       AND starts_with(value, public_base || '/') THEN
        encoded := split_part(split_part(substr(value, length(public_base) + 2), '?', 1), '#', 1);
    END IF;
    IF encoded IS NULL OR encoded = '' OR parts[4] IS NOT NULL THEN
        IF value ~* '[?&]X-Amz-' OR host ~ '\.(r2\.cloudflarestorage\.com|r2\.dev)$'
           OR host = lower(regexp_replace(endpoint, '^https?://([^/]+).*$', '\1', 'i'))
           OR host = lower(regexp_replace(public_base, '^https?://([^/]+).*$', '\1', 'i')) THEN
            RAISE EXCEPTION 'Cannot normalize R2 image URL (query redacted): %', split_part(value, '?', 1);
        END IF;
        RETURN value;
    END IF;
    WHILE position <= length(encoded) LOOP
        IF substr(encoded, position, 1) = '%' THEN
            IF substr(encoded, position + 1, 2) !~ '^[0-9a-fA-F]{2}$' THEN
                RAISE EXCEPTION 'Invalid percent encoding in R2 image path: %', path;
            END IF;
            decoded := decoded || decode(substr(encoded, position + 1, 2), 'hex');
            position := position + 3;
        ELSE
            decoded := decoded || convert_to(substr(encoded, position, 1), 'UTF8');
            position := position + 1;
        END IF;
    END LOOP;
    path := convert_from(decoded, 'UTF8');
    IF starts_with(path, '/') THEN
        RAISE EXCEPTION 'Ambiguous R2 image key: %', path;
    END IF;
    RETURN path;
END;
$$;

CREATE OR REPLACE FUNCTION pg_temp.r2_image_metadata(value JSONB)
RETURNS JSONB LANGUAGE plpgsql AS $$
DECLARE
    result JSONB := value;
    field TEXT;
    items JSONB;
BEGIN
    FOREACH field IN ARRAY ARRAY['image_url', 'image_r2_key'] LOOP
        IF jsonb_typeof(value -> field) = 'string' THEN
            result := jsonb_set(result, ARRAY[field], to_jsonb(pg_temp.r2_image_key(value ->> field)));
        END IF;
    END LOOP;
    FOREACH field IN ARRAY ARRAY['gallery', 'images'] LOOP
        IF jsonb_typeof(value -> field) = 'array' THEN
            SELECT coalesce(jsonb_agg(
                CASE WHEN jsonb_typeof(item) = 'string'
                    THEN to_jsonb(pg_temp.r2_image_key(item #>> '{}'))
                    ELSE item END ORDER BY ordinal
            ), '[]'::jsonb) INTO items
            FROM jsonb_array_elements(value -> field) WITH ORDINALITY AS entries(item, ordinal);
            result := jsonb_set(result, ARRAY[field], items);
        END IF;
    END LOOP;
    RETURN result;
END;
$$;

UPDATE commerce.categories
SET image_r2_key = pg_temp.r2_image_key(image_r2_key)
WHERE image_r2_key IS DISTINCT FROM pg_temp.r2_image_key(image_r2_key);

UPDATE commerce.products
SET metadata = pg_temp.r2_image_metadata(metadata)
WHERE metadata IS DISTINCT FROM pg_temp.r2_image_metadata(metadata);

UPDATE commerce.bouquet_options
SET image_r2_key = pg_temp.r2_image_key(image_r2_key)
WHERE image_r2_key IS DISTINCT FROM pg_temp.r2_image_key(image_r2_key);

UPDATE commerce.testimonials
SET avatar_r2_key = pg_temp.r2_image_key(avatar_r2_key)
WHERE avatar_r2_key IS DISTINCT FROM pg_temp.r2_image_key(avatar_r2_key);

DROP FUNCTION pg_temp.r2_image_metadata(JSONB);
DROP FUNCTION pg_temp.r2_image_key(TEXT);
