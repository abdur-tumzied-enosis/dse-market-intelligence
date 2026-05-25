-- Change document_chunks embedding to vector(768) for Google text-embedding-004.
-- Safe: table is empty until news chunking pipeline runs.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'document_chunks'
          AND column_name = 'embedding'
          AND udt_name = 'vector'
    ) THEN
        ALTER TABLE document_chunks
            ALTER COLUMN embedding TYPE vector(768)
            USING embedding::text::vector(768);

        ALTER TABLE document_chunks
            ALTER COLUMN model SET DEFAULT 'text-embedding-004';
    END IF;
END $$;
