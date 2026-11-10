# Examples

```sh
bhap workspace create contoh --use
bhap add examples/materi --recursive --yes
bhap ask "Apa syarat bobot pada algoritma Dijkstra?"
bhap ask "Apa beda ERD konseptual dan model relasional?" --evidence
bhap ask "Siapa penemu fotosintesis?"          # → abstains: not in the material
bhap study quiz "normalisasi" --count 3
bhap graph
```
