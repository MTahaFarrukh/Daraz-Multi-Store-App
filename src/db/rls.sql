-- Optional defense-in-depth RLS for MultiStore (Supabase).
--
-- Architecture:
--   Backend FastAPI is authoritative for tenancy (JWT + workspace membership).
--   RLS is defense-in-depth if a restricted DB role is ever used from the browser
--   or a compromised client. The API should normally use a service role / DB role
--   that bypasses RLS (or is granted BYPASSRLS).
--
-- DO NOT claim these policies are verified as deployed on Supabase.
-- Apply manually after reviewing roles. All statements are idempotent where practical.
--
-- Enable only after service-role / authenticated roles are understood.

-- Helper: membership check (stable across tables).
CREATE OR REPLACE FUNCTION public.multistore_is_workspace_member(ws uuid)
RETURNS boolean
LANGUAGE sql
STABLE
AS $$
  SELECT EXISTS (
    SELECT 1 FROM workspace_members m
    WHERE m.workspace_id = ws AND m.user_id = auth.uid()
  );
$$;

ALTER TABLE workspaces ENABLE ROW LEVEL SECURITY;
ALTER TABLE workspace_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE daraz_stores ENABLE ROW LEVEL SECURITY;
ALTER TABLE store_groups ENABLE ROW LEVEL SECURITY;
ALTER TABLE store_group_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE store_performance_monthly ENABLE ROW LEVEL SECURITY;
ALTER TABLE daraz_orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE daraz_order_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE order_label_prints ENABLE ROW LEVEL SECURITY;
ALTER TABLE print_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE daraz_products ENABLE ROW LEVEL SECURITY;
ALTER TABLE daraz_product_variants ENABLE ROW LEVEL SECURITY;
ALTER TABLE workspace_product_defaults ENABLE ROW LEVEL SECURITY;
ALTER TABLE product_create_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE workspace_audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE trusted_workspace_connections ENABLE ROW LEVEL SECURITY;
ALTER TABLE finance_transactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE finance_payouts ENABLE ROW LEVEL SECURITY;

-- Drop/recreate named policies for idempotent re-apply
DO $$
DECLARE
  pol text;
BEGIN
  FOR pol IN
    SELECT policyname FROM pg_policies
    WHERE schemaname = 'public'
      AND policyname LIKE 'ms_%'
  LOOP
    EXECUTE format('DROP POLICY IF EXISTS %I ON %I', pol,
      (SELECT tablename FROM pg_policies WHERE policyname = pol AND schemaname = 'public' LIMIT 1));
  END LOOP;
END $$;

CREATE POLICY ms_workspace_members_select_own ON workspace_members
  FOR SELECT TO authenticated
  USING (user_id = auth.uid());

CREATE POLICY ms_workspaces_select_member ON workspaces
  FOR SELECT TO authenticated
  USING (public.multistore_is_workspace_member(id));

CREATE POLICY ms_daraz_stores_member ON daraz_stores
  FOR ALL TO authenticated
  USING (public.multistore_is_workspace_member(workspace_id))
  WITH CHECK (public.multistore_is_workspace_member(workspace_id));

CREATE POLICY ms_store_groups_member ON store_groups
  FOR ALL TO authenticated
  USING (public.multistore_is_workspace_member(workspace_id))
  WITH CHECK (public.multistore_is_workspace_member(workspace_id));

CREATE POLICY ms_store_group_members_via_group ON store_group_members
  FOR ALL TO authenticated
  USING (
    group_id IN (
      SELECT g.id FROM store_groups g
      WHERE public.multistore_is_workspace_member(g.workspace_id)
    )
  )
  WITH CHECK (
    group_id IN (
      SELECT g.id FROM store_groups g
      WHERE public.multistore_is_workspace_member(g.workspace_id)
    )
  );

CREATE POLICY ms_store_perf_member ON store_performance_monthly
  FOR ALL TO authenticated
  USING (public.multistore_is_workspace_member(workspace_id))
  WITH CHECK (public.multistore_is_workspace_member(workspace_id));

CREATE POLICY ms_orders_member ON daraz_orders
  FOR ALL TO authenticated
  USING (public.multistore_is_workspace_member(workspace_id))
  WITH CHECK (public.multistore_is_workspace_member(workspace_id));

CREATE POLICY ms_order_items_member ON daraz_order_items
  FOR ALL TO authenticated
  USING (public.multistore_is_workspace_member(workspace_id))
  WITH CHECK (public.multistore_is_workspace_member(workspace_id));

CREATE POLICY ms_label_prints_member ON order_label_prints
  FOR ALL TO authenticated
  USING (public.multistore_is_workspace_member(workspace_id))
  WITH CHECK (public.multistore_is_workspace_member(workspace_id));

CREATE POLICY ms_print_jobs_member ON print_jobs
  FOR ALL TO authenticated
  USING (public.multistore_is_workspace_member(workspace_id))
  WITH CHECK (public.multistore_is_workspace_member(workspace_id));

CREATE POLICY ms_products_member ON daraz_products
  FOR ALL TO authenticated
  USING (public.multistore_is_workspace_member(workspace_id))
  WITH CHECK (public.multistore_is_workspace_member(workspace_id));

CREATE POLICY ms_variants_member ON daraz_product_variants
  FOR ALL TO authenticated
  USING (public.multistore_is_workspace_member(workspace_id))
  WITH CHECK (public.multistore_is_workspace_member(workspace_id));

CREATE POLICY ms_product_defaults_member ON workspace_product_defaults
  FOR ALL TO authenticated
  USING (public.multistore_is_workspace_member(workspace_id))
  WITH CHECK (public.multistore_is_workspace_member(workspace_id));

CREATE POLICY ms_create_attempts_member ON product_create_attempts
  FOR ALL TO authenticated
  USING (public.multistore_is_workspace_member(workspace_id))
  WITH CHECK (public.multistore_is_workspace_member(workspace_id));

CREATE POLICY ms_audit_member ON workspace_audit_events
  FOR SELECT TO authenticated
  USING (public.multistore_is_workspace_member(workspace_id));

CREATE POLICY ms_connections_member ON trusted_workspace_connections
  FOR ALL TO authenticated
  USING (
    public.multistore_is_workspace_member(source_workspace_id)
    OR public.multistore_is_workspace_member(destination_workspace_id)
  )
  WITH CHECK (
    public.multistore_is_workspace_member(source_workspace_id)
    OR public.multistore_is_workspace_member(destination_workspace_id)
  );

CREATE POLICY ms_finance_txn_member ON finance_transactions
  FOR ALL TO authenticated
  USING (public.multistore_is_workspace_member(workspace_id))
  WITH CHECK (public.multistore_is_workspace_member(workspace_id));

CREATE POLICY ms_finance_payout_member ON finance_payouts
  FOR ALL TO authenticated
  USING (public.multistore_is_workspace_member(workspace_id))
  WITH CHECK (public.multistore_is_workspace_member(workspace_id));
