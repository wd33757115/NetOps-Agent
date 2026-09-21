BEGIN;
SELECT version_num AS alembic_before FROM public.alembic_version;
DO $$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT t.tgname, n.nspname, c.relname
    FROM pg_trigger t
    JOIN pg_class c ON c.oid = t.tgrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_proc p ON p.oid = t.tgfoid
    WHERE NOT t.tgisinternal AND p.proname LIKE 'knowledge\_%'
  LOOP
    EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I.%I', r.tgname, r.nspname, r.relname);
    RAISE NOTICE 'dropped trigger %.%.%', r.nspname, r.relname, r.tgname;
  END LOOP;
END $$;
DROP TABLE IF EXISTS
  public.knowledge_feedback_reviews,
  public.knowledge_answer_feedback,
  public.knowledge_answer_history,
  public.knowledge_parse_review_artifacts,
  public.knowledge_query_leases,
  public.knowledge_retrieval_receipts,
  public.knowledge_index_runtimes,
  public.knowledge_generation_changes,
  public.knowledge_publication_reviews,
  public.knowledge_index_identities,
  public.knowledge_chunks,
  public.knowledge_chunk_sets,
  public.knowledge_uploads,
  public.knowledge_index_outbox,
  public.knowledge_index_attempts,
  public.knowledge_index_jobs,
  public.knowledge_index_head,
  public.knowledge_index_generations,
  public.knowledge_revision_states,
  public.knowledge_revision_builds,
  public.knowledge_revisions,
  public.knowledge_documents,
  public.knowledge_audit_events,
  public.knowledge_access_changes,
  public.knowledge_app_bindings,
  public.knowledge_space_grants,
  public.knowledge_spaces,
  public.knowledge_authz_epoch,
  public.knowledge_identity_uses
CASCADE;
DROP TABLE IF EXISTS
  django.bff_knowledgecustomerchannel,
  django.bff_knowledgeservicesession,
  django.bff_knowledgeserviceidentity,
  django.bff_knowledgerecoverypermit,
  django.bff_knowledgeaccountstate,
  django.bff_knowledgesession,
  django.bff_knowledgegroupprofile
CASCADE;
DO $$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT n.nspname, p.proname, pg_get_function_identity_arguments(p.oid) AS args
    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE p.proname LIKE 'knowledge\_%' AND n.nspname IN ('public', 'django')
  LOOP
    EXECUTE format('DROP FUNCTION IF EXISTS %I.%I(%s) CASCADE', r.nspname, r.proname, r.args);
    RAISE NOTICE 'dropped function %.%', r.nspname, r.proname;
  END LOOP;
END $$;
UPDATE public.alembic_version SET version_num = '0101_patrol_report_templates';
DELETE FROM django.django_migrations
 WHERE app = 'bff' AND name <> '0001_desktop_preference';
SELECT 'table' AS kind, schemaname || '.' || tablename AS name FROM pg_tables
 WHERE tablename LIKE 'knowledge\_%' OR tablename LIKE 'bff\_knowledge%'
UNION ALL
SELECT 'function', n.nspname || '.' || p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
 WHERE p.proname LIKE 'knowledge\_%';
COMMIT;
