# Keep file-pin owner on soft 1011 reconnect

## Purpose

Close the HTTP-bridge reconnect hole where a live `input_file.file_id` pin is
treated as skippable prompt-cache locality after upstream close `1011`.

## Decision

Honor `file_required_preferred_account` in reconnect owner resolution, and
pass it from submit-on-closed fresh-upstream retry. Do not persist pins
across replicas here.

## Constraints

File pins are hard ownership. Soft `1011` skip-same-account stays valid only
when no live file pin (and no other required owner) is present.

## Failure mode

If the pin account is excluded or cannot reconnect, fail closed with the
existing required-owner unavailable error. Do not fall back to another
account and forward the `file_id`.

## Example

Upload `file_xyz` on account A, then send `/v1/responses` with that
`input_file` on a soft prompt-cache bridge session. Upstream closes `1011`
before the next turn is accepted. Reconnect must keep account A required.
