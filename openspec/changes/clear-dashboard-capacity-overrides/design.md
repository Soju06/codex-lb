# Design

## Backend Contract

Keep the current effective fields in `DashboardSettingsResponse`, add four
nullable `...Override` fields, and expose four environment baseline fields for
prospective clear validation. The effective values continue to be resolved by
`SettingsService` from the stored nullable columns and process settings.

`DashboardSettingsUpdateRequest` already preserves explicit-null intent through
Pydantic `model_fields_set`. The API layer will convert that intent into
explicit clear flags in the service update data. This avoids overloading
`None` in repository method arguments, where `None` currently means "do not
update".

The repository will apply each field in this order:

1. clear the column when its clear flag is true;
2. otherwise store a supplied numeric override;
3. otherwise leave the column untouched.

The API validates a clear against the corresponding environment baseline before
persistence, while the existing row-version CAS and settings cache invalidation
remain unchanged.

## Frontend Contract

Routing settings will initialize each input from its raw override. A `NULL` raw
override renders as an empty input, while the effective value remains resolved
from the raw override or environment. Clearing an input sends explicit `null`;
entering a number sends that number. The card sends only fields changed by the
operator.

The four fields share one existing capacity save action. Validation must allow
an empty value as a clear operation while validating the effective candidate
stream limit and recovery reserve relationship.

## Compatibility

The effective response fields retain their names and types. New raw override
fields are additive. Existing update clients that omit the new explicit-null
fields continue to behave as before.
