# Sample data

`sample_courses.json` is a hand-written dummy catalog for experimenting with the scheduler. The courses and times are made up. It is also used for manual runs, the smoke test and parser name matching. **Tests never assert exact results against it** — correctness tests use tiny catalogs from `tests/helpers/catalog_builder.py`.

8 required courses (`"required": true`, which become default `must_take` constraints; 22 credits) + 8 electives. Week is the default Sun–Fri.

## What each course demonstrates
| ID | Course | Shows |
|---|---|---|
| ECP | Emergency Command Procedures | a single group — no choice |
| OLD | Officer Leadership Development | three components (lecture, tutorial, lab); lecture meets twice a week (Mon + Wed) |
| PC2 | Physical Conditioning II | both lecture groups meet twice a week; labs 11 and 12 meet at identical times → merged into one option "11 or 12" |
| NAV2 | Starship Navigation Principles II | lecture 1 (Tue 10–12) is back-to-back with SHO lecture 1 |
| SHO | Shuttlecraft Operation | lecture 1 (Tue 12–14), the other half of the back-to-back pair |
| ZGC | Zero-Gravity Combat Techniques | Friday mornings only → "fridays off" is infeasible |
| GVO | Ground Vehicle Operation | labs 11/12/21/22 numbered by `parent`; any lab may pair with any lecture |
| GVM | Ground Vehicle Maintenance | name close to GVO (fuzzy-match trap) |
| APA | Advanced Pathology | lecture 2 has `attendance_required: false` (ignored by the solver in v1) |
| XPA | Xenopathology | name close to APA and XBI; lecture meets twice a week (Sun + Tue) |
| HEP / HEW | High-Energy Particle Physics / Weapons Systems | similar names; HEW's only lab is Fri 08–10 |
| SHM | Shuttlecraft Maintenance | name close to SHO |
| CCY | Computer Cybernetics | taught by Cohn — near-miss for "Cohen" |
| HELM2 | Starship Helm Operations II | taught by Cohen, alongside OLD, NAV2 and APA |
| XBI | Xenobiology | both lecture groups meet twice a week; lecture 1 clashes with NAV2 lecture 1 |

A group with several sessions is all-or-nothing: choosing it means attending every session, and a clash with any one of them rules out the whole group.

Instructors: Adler, Brenner, Cohen, Cohn, Dagan, Erez, Farkas, Gal.

## Known counts (required courses only, groups merged)
Checked by brute force when the file was written; update if you edit the file.
- No constraints: 1,216 clash-free schedules; 352 of them use the NAV2/SHO back-to-back pair.
- "no classes before 10, keep 12-13 free": 3 schedules (with K=5 this also shows the "only 3 possible" note).
- "fridays off": 0 — the conflict should name ZGC.

## Manual test phrases
- Feasible: `no classes before 10, prefer Cohen, keep 12-13 free`
- Conflict demo: `no classes before 10, fridays off, prefer Cohen, keep 12-13 free` — infeasible while ZGC is required (switch ZGC off in the picker and it solves).
