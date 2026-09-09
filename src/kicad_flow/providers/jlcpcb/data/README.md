# Local catalogue data

This directory is intentionally empty in Git. The downloaded JLCPCB SQLite
catalogue is ignored because it is large and updated independently of
KiCadFlow.

Download
<https://cdfer.github.io/jlcpcb-parts-database/jlcpcb-components.sqlite3> and
save it in this directory as `jlcpcb-components.sqlite3`. The search provider
opens that file read-only. Set `KICAD_FLOW_JLCPCB_DATABASE` only when an
installation needs the database elsewhere or an integration example supplies a
small fixture.
