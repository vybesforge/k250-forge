# Assets (ships empty — nothing personal is distributed)

All persona imagery lives here, namespaced per slug:

| pattern | what |
|---|---|
| `plates_<slug>_<unixtime>.png` | generations; **newest wins** the portrait frame |
| `expr_<slug>_<mood>.png` | expression crossfade states (calm/stern/pleased/hungry/soft/cruel) |
| `ref_<slug>.png` | reference plate for `i2i` / `edit` personas (point `gen.reference` at it) |

Set `VFK250_ASSETS=/some/dir` to keep assets outside the repo (recommended —
generations accumulate fast and you may not want them in git).
