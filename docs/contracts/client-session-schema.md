# Client Session Contract

`client-session/v1` describes one sanitized daemon client role. Its exact
fields are `schema_version`, `mode`, `client_id`, `role`, `lease_generation`,
`compatible`, `write_enabled`, `blockers`, and `controls`.

Observers never gain write authority. `write_enabled` is true only for a
compatible controller session, and every mutation must still present and pass
the daemon's current lease and safety gates. Native connection handles, socket
paths, process IDs, credentials, and raw protocol frames are excluded.
