# Mission Scheduler Contract

`mission-scheduler/v1` is the compact scheduler observation contract. Its exact
fields are `schema_version`, `mode`, `state`, `active_mission_id`, `active_step`,
`next_transition`, `blockers`, and `controls`.

The daemon derives this surface from durable Mission facts. `next_transition`
is `null` or one of `start_worker`, `prepare_dispatch`, `dispatch_prepared`,
`await_worker`, `validate_reply`, `record_handoff`, `activate_next`,
`wait_human`, `wait_ambiguity`, `blocked`, `complete_mission`, or `idle`.
The discovery example is an active admitted Mission whose next bounded
transition is `start_worker`; background scheduling is no longer advertised as
an inactive M2a placeholder. `start_worker` is selected when an admitted frozen
Mission authorizes one exact tmux Worker startup. That
transition must persist a start claim before touching tmux; a claimed start
without a terminal receipt is ambiguous and must never be replayed.

Before any attempt is prepared, the scheduler reloads current project config
and compares the Worker provider/role/workspace/transport plus compact runtime
identity against frozen authority. Any drift is a visible blocker for both ACP
and tmux and prevents transport construction.

tmux pane existence is not readiness. Before dispatch, the daemon probes the
trusted CLI chrome through the configured project tmux runtime. First-run trust,
login, or other setup screens remain blocked for explicit human setup; the
daemon never answers them or sends the Mission prompt into setup UI.
