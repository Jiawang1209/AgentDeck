# Mission Scheduler Contract

`mission-scheduler/v1` is the compact scheduler observation contract. Its exact
fields are `schema_version`, `mode`, `state`, `active_mission_id`, `active_step`,
`next_transition`, `blockers`, and `controls`.

The daemon derives this surface from durable Mission facts. `next_transition`
may expose the pure scheduler's bounded transition, including `start_worker`
when an admitted frozen Mission authorizes one exact tmux Worker startup. That
transition must persist a start claim before touching tmux; a claimed start
without a terminal receipt is ambiguous and must never be replayed.

tmux pane existence is not readiness. Before dispatch, the daemon probes the
trusted CLI chrome through the configured project tmux runtime. First-run trust,
login, or other setup screens remain blocked for explicit human setup; the
daemon never answers them or sends the Mission prompt into setup UI.
