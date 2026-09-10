-- Optional defense-in-depth RLS for Phase 1A (Supabase).
-- Backend still enforces authorization via JWT + membership checks.
-- Enable only after service-role / authenticated roles are understood.
--
-- Recommended: run API with a restricted DB role; keep service role off the browser.

ALTER TABLE workspaces ENABLE ROW LEVEL SECURITY;
ALTER TABLE workspace_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE daraz_stores ENABLE ROW LEVEL SECURITY;
ALTER TABLE store_groups ENABLE ROW LEVEL SECURITY;
ALTER TABLE store_group_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE print_jobs ENABLE ROW LEVEL SECURITY;

-- Example policies for authenticated JWT users (auth.uid()).
-- Adjust if the API uses a service role (service role bypasses RLS).

CREATE POLICY workspace_members_select_own ON workspace_members
  FOR SELECT TO authenticated
  USING (user_id = auth.uid());

CREATE POLICY workspaces_select_member ON workspaces
  FOR SELECT TO authenticated
  USING (
    id IN (SELECT workspace_id FROM workspace_members WHERE user_id = auth.uid())
  );

CREATE POLICY daraz_stores_member_all ON daraz_stores
  FOR ALL TO authenticated
  USING (
    workspace_id IN (SELECT workspace_id FROM workspace_members WHERE user_id = auth.uid())
  )
  WITH CHECK (
    workspace_id IN (SELECT workspace_id FROM workspace_members WHERE user_id = auth.uid())
  );

CREATE POLICY store_groups_member_all ON store_groups
  FOR ALL TO authenticated
  USING (
    workspace_id IN (SELECT workspace_id FROM workspace_members WHERE user_id = auth.uid())
  )
  WITH CHECK (
    workspace_id IN (SELECT workspace_id FROM workspace_members WHERE user_id = auth.uid())
  );

CREATE POLICY store_group_members_via_group ON store_group_members
  FOR ALL TO authenticated
  USING (
    group_id IN (
      SELECT g.id FROM store_groups g
      JOIN workspace_members m ON m.workspace_id = g.workspace_id
      WHERE m.user_id = auth.uid()
    )
  )
  WITH CHECK (
    group_id IN (
      SELECT g.id FROM store_groups g
      JOIN workspace_members m ON m.workspace_id = g.workspace_id
      WHERE m.user_id = auth.uid()
    )
  );

CREATE POLICY print_jobs_member_all ON print_jobs
  FOR ALL TO authenticated
  USING (
    workspace_id IN (SELECT workspace_id FROM workspace_members WHERE user_id = auth.uid())
  )
  WITH CHECK (
    workspace_id IN (SELECT workspace_id FROM workspace_members WHERE user_id = auth.uid())
  );
