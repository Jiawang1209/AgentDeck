# Client Session Contract

`client-session/v1` describes one sanitized daemon client role. Its exact
fields are `schema_version`, `mode`, `client_id`, `role`, `lease_generation`,
`compatible`, `write_enabled`, `blockers`, and `controls`.

Observers require a non-empty client identity and never carry a lease generation
or write authority. Controllers require a non-empty client identity and a
positive integer lease generation. The `none` role carries neither identity nor
lease generation. `write_enabled` is true only for a compatible controller
session, and incompatible sessions are always read-only. Every mutation must
still present and pass the daemon's current lease and safety gates. Native
connection handles, socket paths, process IDs, credentials, and raw protocol
frames are excluded.

`controller.acquire` is the only lease-exempt mutation and exists solely to
bootstrap a verified compatible client when no unexpired controller is held;
it never performs takeover. `controller.renew` and `controller.release` require
the exact current lease. Grant, renew, release, and expiry each commit through
StateStore and synchronously flush the strict daemon event outbox before success
is returned. A failed automatic stop releases only the temporary controller it
acquired; credentials explicitly supplied by a caller are never auto-released.
If cleanup cannot be confirmed, the CLI reports an explicit cleanup blocker.
These RPC credentials remain internal to the stop flow and do not add fields or
controls to this card.
