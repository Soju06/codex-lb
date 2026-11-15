## Evidence and scope

The shared `ResponsesRequest` validator rejects `output.encrypted_content` while accepting `reasoning.encrypted_content`. Public Responses conversion and native HTTP/WebSocket requests reach this validator. The selector is forwarded as an opaque upstream option; it is not an alias for reasoning encryption.

For example, `["reasoning.encrypted_content", "output.encrypted_content"]` must retain both entries in order. Regression tests use synthetic requests and stubbed upstream transports, proving local acceptance and forwarding without claiming live provider validation or decryptability.
