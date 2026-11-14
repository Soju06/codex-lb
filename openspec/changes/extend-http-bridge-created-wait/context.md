# Context

A local DSH image request (request-log 6774) failed after 60.058 seconds without response.created. The selected change raises the existing shared cap to 100 seconds instead of adding an image-only configuration or replay branch.

This does not guarantee upstream acceptance. A request still silent after 100 seconds fails under the existing policy; an image request with ambiguous acceptance MUST NOT be automatically replayed. Example: send at t=100, stuck threshold=300 gives deadline t=200; threshold=30 still gives t=130.
