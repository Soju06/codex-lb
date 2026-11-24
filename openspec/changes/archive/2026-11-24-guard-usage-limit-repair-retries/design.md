## Approach

Compare affinity evidence at the provisional write and keep the existing guarded rollback. Release local admission before reporting any rollback error. Save a pending relative target in the same durable state used for the initial repair request.
