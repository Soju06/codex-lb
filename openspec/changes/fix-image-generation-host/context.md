# Evidence and scope

`POST /v1/images/generations` and `POST /v1/images/edits` currently translate
valid requests into an internal Responses request hosted by `gpt-5.6-luna`.
The upstream response is HTTP 400 with `Tool choice 'image_generation' not
found in 'tools' parameter`. The equivalent Responses request succeeds when
hosted by `gpt-5.6-sol`.

The change separates Images host selection from default account probes. It does
not change the public image model, multipart-to-`input_image` conversion,
account selection, usage settlement, transport choice, or retry policy.
