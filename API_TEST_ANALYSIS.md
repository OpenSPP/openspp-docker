# API Test Analysis: Configuration vs. ORM Issues

## Key Finding: **Some API Tests DO Pass!**

### ✅ **Working API Endpoints:**

- **OAuth Endpoint** (`/api/v2/spp/oauth/token`) - **PASSING**
  - `test_token_generation_success` ✅ (200 status, token generated)
  - `test_token_no_scopes` ✅ (200 status)
  - `test_invalid_grant_type` ✅ (400 status as expected)
  - Only `test_token_jwt_payload` fails due to JWT audience validation (not endpoint
    issue)

### ❌ **Failing API Endpoints:**

- **Group API Endpoints** - All 22 tests failing with `[Error]`
- **Individual API Endpoints** - 12 tests failing (mostly 403 errors)
- **Metadata Endpoint** - 9 tests failing (structure issues)

---

## Root Cause Analysis

### **NOT a FastAPI Configuration Issue**

The FastAPI setup is **working correctly**:

- ✅ FastAPI endpoints are registered (`fastapi_endpoint.xml`)
- ✅ Routers are properly imported and added
- ✅ OAuth endpoint successfully handles HTTP requests
- ✅ Routing map is generated correctly

### **Actual Issue: Odoo ORM Database Flush Error**

The Group API endpoint failures are caused by:

```
AttributeError: 'Query' object has no attribute 'get_sql'
```

**Error Location:**

- Happens during `self.cr.flush()` in `url_open()`
- Triggered by `_recompute_all()` → `_recompute_field()`
- Fails in `mail_thread.py` → `_compute_field_value()`
- Related to computed field recomputation during database flush

**Why OAuth Works But Group/Individual Don't:**

- OAuth endpoint doesn't trigger the same database flush/computation cycle
- Group/Individual endpoints likely create/update records that trigger mail_thread
  computed fields
- The computed field recomputation fails due to Odoo 19 ORM changes

---

## Evidence

### OAuth Endpoint Success:

```
2025-11-29 08:19:10,295 INFO werkzeug: "POST /api/v2/spp/oauth/token HTTP/1.1" 200
```

### Group Endpoint Failure:

```
File "/opt/odoo/auto/addons/spp_api_v2/tests/test_group_api.py", line 236, in test_create_group_success
    response = self.url_open(...)
  ...
  File "/opt/odoo/custom/src/odoo/odoo/tests/common.py", line 2129, in request
    self.cr.flush()
  ...
AttributeError: 'Query' object has no attribute 'get_sql'
```

---

## Conclusion

**This is NOT a FastAPI configuration issue.** The API endpoints are properly configured
and accessible. The failures are due to:

1. **Odoo 19 ORM Compatibility Issue** - `Query` object API changes
2. **Computed Field Recomputation** - Failing during database flush
3. **Mail Thread Integration** - Computed fields in mail_thread module causing issues

### Recommended Fixes:

1. **Investigate Odoo 19 Query API Changes**

   - Check if `Query.get_sql()` was renamed or removed
   - Look for Odoo 19 migration notes on ORM changes

2. **Fix Computed Field Recomputation**

   - Check mail_thread computed fields
   - May need to update field computation logic for Odoo 19

3. **Individual Endpoint 403 Errors**

   - These are separate authentication/authorization issues
   - Not related to the ORM flush problem

4. **Metadata Endpoint Structure**
   - Missing fields in FHIR CapabilityStatement
   - Separate issue from ORM problems

---

## Test Status Summary

| Endpoint Type  | Status     | Issue Type                    |
| -------------- | ---------- | ----------------------------- |
| OAuth          | ✅ Working | None (1 JWT validation issue) |
| Group API      | ❌ Failing | ORM flush error               |
| Individual API | ❌ Failing | Auth (403) + ORM flush        |
| Metadata       | ❌ Failing | Structure incomplete          |

**FastAPI Configuration:** ✅ **Working Correctly**
