# Mission Scheduler Contract

`mission-scheduler/v1` is the compact scheduler observation contract. Its exact
fields are `schema_version`, `mode`, `state`, `active_mission_id`, `active_step`,
`next_transition`, `blockers`, and `controls`.

M2a deliberately reports the scheduler as inactive with the blocker
`background Mission scheduling is not implemented in M2a`. It does not advance
a Mission, dispatch a Worker, or authorize a transition. M2b will populate the
same surface from durable scheduler facts.
