# Remaining Test Failures in spp_api_v2 Module

## Status

- **Current Pass Rate:** 98.0% (491/501 tests passing)
- **Remaining Failures:** 5 tests in `spp_api_v2` module
- **Other Modules:** 5 failures in `spp_registry_base` and `spp_vocabulary` (not in
  scope for this ticket)

## Issue 1: Group Member Role is None

**Test:** `TestGroupAPIEndpoints.test_read_group_member_with_role` **Error:**
`TypeError: 'NoneType' object is not subscriptable` **Location:**
`spp_api_v2/tests/test_group_api.py:112`

**Problem:** The test expects group members to have a `role` field with
`coding[0]["code"] == "head"`, but the role is `None`. The test accesses
`member["role"]["coding"][0]["code"]` which fails when role is None.

**Root Cause:** The `_build_member()` method in `spp_api_v2/services/group_service.py`
is not finding the vocabulary code for membership types. The search logic tries multiple
strategies:

1. Match by code (membership_type.name.lower().replace(" ", "-"))
2. Match by display name
3. Match by namespace_uri="urn:test:relationship"
4. Special case for "Head of Household" → search for code="head"

The test setup creates:

- `spp.group.membership.kind` with `name="Head of Household"`
- `spp.vocabulary.code` with `code="head"`, `display="Head of Household"`,
  `namespace_uri="urn:test:relationship"`

**Investigation Needed:**

- Verify the vocab_code search is actually finding the record
- Check if the membership_type.name matches exactly "Head of Household"
- Verify the vocabulary code exists in the test database at the time of the search
- Consider adding debug logging to see which search strategy (if any) finds the code

**Suggested Fix:**

- Ensure the vocab_code search includes the vocabulary_id filter if needed
- Add fallback to create role from membership type name if vocab_code not found (already
  exists but may not be working)
- Verify the test setup creates the vocab_code before the group is read

---

## Issue 2: No-Consent Response Includes None Values (Group)

**Test:** `TestGroupAPIEndpoints.test_read_group_no_consent_minimal_data` **Error:**
`AssertionError: 'name' unexpectedly found in {'resourceType': 'Group', 'identifier': [...], 'active': True, 'type': 'household', 'name': None, 'member': None, ...}`
**Location:** `spp_api_v2/tests/test_group_api.py:161`

**Problem:** When there's no consent for a group, the API should return only
`resourceType` and `identifier` (minimal response). However, the response includes
fields with `None` values like `'name': None, 'member': None, 'quantity': None`, etc.

**Root Cause:** The `ConsentService.filter_response()` method should return early with
minimal data when no consent is found (lines 102-133), but it appears the consent
service is not detecting "no consent" correctly, or the data passed to it already
contains None values that aren't being filtered out.

**Current Flow:**

1. `group_service.to_api_schema()` builds full data structure (filters None values at
   lines 130-138)
2. `consent_service.filter_response()` should return early if no consent (lines 102-133)
3. But the response still contains None values

**Investigation Needed:**

- Check if `check_api_consent()` is returning a consent record when it shouldn't
- Verify the API client's `legal_basis` - if it's not "consent", it may bypass consent
  check (line 65)
- Check if `_apply_client_scope_filter()` is being called instead of early return
- Verify the test creates a group WITHOUT consent (line 148-151 of test)

**Suggested Fix:**

- Ensure `filter_response()` returns early with ONLY `resourceType` and `identifier` (no
  other fields)
- Verify the early return path is actually being executed (add logging)
- Check if the group service is including None values before passing to consent service

---

## Issue 3: No-Consent Response Includes None Values (Individual)

**Test:** `TestIndividualAPIEndpoints.test_read_individual_no_consent_minimal_data`
**Error:**
`AssertionError: 'name' unexpectedly found in {'resourceType': 'Individual', 'identifier': [...], 'active': True, 'name': None, 'birthDate': None, ...}`
**Location:** `spp_api_v2/tests/test_individual_api.py:138`

**Problem:** Same issue as Issue 2, but for Individual resources. The no-consent
response should only include `resourceType` and `identifier`, but it includes fields
with `None` values.

**Root Cause:** Same as Issue 2 - the consent service is not returning early when no
consent is found for individuals.

**Investigation Needed:**

- Same as Issue 2
- Verify `individual_service.to_api_schema()` filters None values correctly (recently
  added at lines 242-252)

**Suggested Fix:**

- Same as Issue 2
- Ensure individual service filters None values before passing to consent service

---

## Issue 4: Contact Info Test Error

**Test:** `TestIndividualService.test_to_api_schema_contact_info` **Error:** Exception
during `create_test_individual()` call **Location:**
`spp_api_v2/tests/test_individual_service.py:102` → `common.py:196`

**Problem:** The test creates an individual with `phone`, `mobile`, and `email` fields,
but the creation fails. The error occurs in
`spp_registry_membership/models/individual.py:40` during `res.partner.create()`.

**Root Cause:** The error traceback shows it fails during partner creation, likely due
to:

- Field validation error (phone/mobile format validation)
- Missing required fields
- Constraint violation

**Investigation Needed:**

- Check the full traceback in the test log (may be cut off)
- Verify `res.partner` model validation rules for `phone` and `mobile` fields
- Check if phone number format validation is failing (e.g., `+1234567890` may need
  different format)
- Verify all required fields are provided in `create_test_individual()`

**Suggested Fix:**

- Check phone number format requirements (may need country code, specific format)
- Verify `mobile` field exists and accepts the test value
- Add validation error handling or use valid phone number formats
- Check if `spp_registry_membership` model has additional constraints

---

## Issue 5: Photo Test Error

**Test:** `TestIndividualService.test_to_api_schema_photo` **Error:**
`binascii.Error: Incorrect padding` **Location:**
`spp_api_v2/tests/test_individual_service.py:278` → `odoo/fields_binary.py:317`

**Problem:** The test creates an individual with a photo (`image_1920` field), but
Odoo's `_image_process()` method fails when trying to decode the base64 string because
of incorrect padding.

**Root Cause:** The error occurs in Odoo's `fields_binary.py` at line 317:
`img = base64.b64decode(value or '')`. The `create_test_photo()` method returns binary
PNG bytes, but when these bytes are passed to `image_1920`, Odoo expects a
base64-encoded string, not binary bytes. Odoo then tries to decode it as base64, which
fails.

**Current Implementation:**

- `create_test_photo()` returns binary PNG bytes (not base64 string)
- Test passes these bytes directly to `image_1920=photo_data`
- Odoo's `Binary` field expects base64-encoded string, not binary bytes

**Investigation Needed:**

- Verify what format `image_1920` field expects (base64 string vs binary bytes)
- Check if Odoo 19 changed the expected format for binary fields
- Verify the base64 string in `create_test_photo()` is valid if we need to return a
  string

**Suggested Fix:**

- If `image_1920` expects base64 string: Return base64-encoded string from
  `create_test_photo()`, not binary bytes
- If `image_1920` expects binary bytes: Ensure the bytes are valid PNG format (not
  base64-encoded)
- Use the fallback PNG creation (manual bytes) and ensure it's in the correct format
- Check Odoo 19 documentation for `Binary` field format requirements

---

## Files Modified (for reference)

- `spp_api_v2/services/consent_service.py` - Added None value filtering
- `spp_api_v2/services/group_service.py` - Improved vocab_code search, added None
  filtering
- `spp_api_v2/services/individual_service.py` - Added None filtering
- `spp_api_v2/schemas/individual.py` - Made `name` field optional
- `spp_api_v2/tests/common.py` - Fixed photo creation with fallback
- `spp_api_v2/tests/test_consent_history.py` - Fixed reason field access

## Test Log Location

Full test log:
`/private/tmp/spp_api_v2-spp_banking-spp_consent-spp_programs_base-spp_registry_base-spp_regis-20251129-181709.log`

## Next Steps

1. Investigate why consent service isn't returning early for no-consent cases
2. Fix vocab_code search for group member roles
3. Fix contact info and photo test errors (check actual exceptions)
4. Verify all None value filtering is working correctly
