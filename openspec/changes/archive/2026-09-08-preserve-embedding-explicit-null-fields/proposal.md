## Why

Source-routed `/v1/embeddings` requests currently serialize with `exclude_none=True`, so explicitly supplied null extras disappear before forwarding even though the model-source contract requires extras beyond `model` and `input` to pass through verbatim. Compatible sources that distinguish an omitted key from an explicit null therefore receive a client-different payload.

## What Changes

- Preserve explicitly supplied null embedding extras when forwarding to a model source.
- Continue omitting fields the client did not send and preserving non-null extras unchanged.
- Lock the behavior at the real ASGI route and captured-source boundary while retaining reservation settlement and request-log assertions.
- Sync the stable embeddings forwarding requirement into the owning main specification.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `model-source-routing`: Clarify that verbatim embeddings forwarding distinguishes explicitly supplied null fields from omitted fields.

## Impact

The change is limited to `/v1/embeddings` request serialization in `app/modules/proxy/api.py`, focused model-source routing integration coverage, and the `model-source-routing` OpenSpec contract. It adds no setting, dependency, migration, authentication behavior, or dashboard surface.
