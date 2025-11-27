# OpenSPP Odoo 19 Testing Workflow

Practical notes for running, fixing, and re‑running the OpenSPP test suites on
Odoo 19 (Doobda stack).

## Paths & key commands
- Repo root: `~/Projects/134-openspp/openspp-odoo-19-migration`
- Addons we edit: `odoo/custom/src/openspp_modules`
- Test runner (with dependencies):
  - `PATH="$HOME/.local/bin:$PATH" invoke test-spp-deps --modules=<module> --skip=queue_job --mode=init --db-filter='^devel$'`
- Quick module-only run:
  - `PATH="$HOME/.local/bin:$PATH" invoke test --modules=<module> --skip=queue_job --with-deps --db-filter='^devel$'`
- Clean DB (use after schema/group changes):
  - `PATH="$HOME/.local/bin:$PATH" invoke resetdb --dbname=devel --modules=base --no-populate`

## Workflow loop
1) **Reset DB when needed** – after security/XMLID/manifest/model changes.
2) **Run targeted suite** – e.g. `test-spp-deps --modules=spp_mis_demo` or `--modules=spp_base_farmer_registry_demo`.
3) **Inspect failures fast** – `tail -n 200 /tmp/spp-tests.log` (or the tee’d log); search with `rg "ERROR:" /tmp/spp-tests.log`.
4) **Apply minimal fixes** – small patches via `apply_patch`; keep changes scoped.
5) **Re-run** – same `test-spp-deps` command; iterate until green.
6) **Commit & push** – `git status`, add touched files, `git commit -m "…"`, `git push opensppv2 19.0`.

## Odoo 19 gotchas encountered
- `stock.move` create: `name` no longer accepted; use `description_picking`.
- Some record attrs absent (e.g., `scrapped`): guard with `getattr(..., False)` and skip scrap moves.
- `account.account` now uses `company_ids` (m2m) for outstanding/transfer accounts.
- Group XMLIDs renamed: prefer `spp_security.group_spp_admin` and `spp_registry_base.group_registry_officer`; keep deprecated aliases where legacy XMLIDs remain.
- GIS/PostGIS in tests: under `config.get("test_enable")`, skip real PostGIS; create dummy `geometry` type so models install.
- Queue_job excluded: always pass `--skip=queue_job` to these runs.

## Farmer registry demo specifics (recent fixes)
- Added deprecated alias `spp_registry_base.group_spp_admin` → implies `spp_security.group_spp_admin`.
- Updated demo menus/data export/import views to use `spp_security.group_spp_admin` + `spp_registry_base.group_registry_officer`.
- Land record ACL uses `spp_security.group_spp_admin`.
- GIS init guard + dummy `geometry` type to avoid PostGIS requirement during tests.

## Log tips
- Install-time failures: look ~30–50 lines above `Failed to initialize database 'devel'` for the offending XML/view/ACL.
- Missing XMLID group: search/replace with new group IDs or add a compat record.
- Geometry errors: ensure geometry strings parse, or rely on dummy type in tests.

## When to reset DB
- After touching security/groups/XMLIDs, manifests, or core models.
- After install-time errors.
- Otherwise reuse the same DB to save time.

## One-liner checklist
```
resetdb --dbname=devel --modules=base --no-populate
test-spp-deps --modules=<target> --skip=queue_job --mode=init --db-filter='^devel$'
tail /tmp/spp-tests.log | rg "ERROR:" -C2
patch → rerun → repeat
git status && git commit && git push
```

