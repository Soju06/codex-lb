## Context

The dashboard picker renders `GET /api/models` without adding model IDs locally. That endpoint lists public subscription models and enabled source models. Image models are validated by a separate Images adapter allowlist and usually absent from the subscription catalog.

## Goals / Non-Goals

**Goals:** Make every supported Images model selectable in API-key allowlists, including during bootstrap or an empty catalog; preserve selection on save and reopen.

**Non-Goals:** Change public proxy catalogs, account eligibility, upstream image permissions, image routing, or add settings.

## Decisions

- Export the union of existing Images validation sets and reuse it in validation and the dashboard listing. A frontend list would duplicate the backend contract.
- Supplement only the dashboard catalog. Adding images to the Responses registry would advertise them as conversational host models and affect routing.
- Keep public subscription entries on collisions; add missing image entries before sources with `sourceOnly=false`, empty supported reasoning efforts and a null default effort. Preserve existing source deduplication rules.
- Use typed dashboard model response schemas for the touched endpoint, preserving its wire shape and existing metadata.
- Mark adapter-provided entries `imageOnly=true`. Automations also consumes this catalog, so exclude image-only entries alongside source-only entries from its Responses model picker. Preserve compatibility for clients and catalog entries that omit this optional marker.

## Risks / Trade-offs

- A listed image model may be unavailable to the selected upstream account. Listing expresses adapter support; actual Images requests retain authorization, account selection and upstream error handling.
- A refreshed or empty registry could hide options again. Regression tests cover bootstrap, refreshed, empty, and overlapping catalogs.
- Dashboard and public proxy model sets intentionally differ for these adapter entries. Update the consistency requirement and test to scope equality to subscription entries.

## Migration Plan

No data migration or configuration is needed. Deploy through the existing application deployment workflow when requested; the dashboard query refreshes its model list after its existing cache interval or a page reload.
