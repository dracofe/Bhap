# Algoritma Dijkstra

## Definisi
Algoritma Dijkstra mencari jalur terpendek dari satu simpul sumber ke semua simpul lain pada graf berbobot.

## Syarat
Dijkstra mensyaratkan semua bobot sisi bernilai non-negatif. Untuk bobot negatif gunakan algoritma Bellman-Ford.

## Langkah
1. Beri jarak 0 pada simpul sumber dan tak hingga pada simpul lain.
2. Pilih simpul belum dikunjungi dengan jarak sementara terkecil.
3. Perbarui (relaksasi) jarak tetangganya.
4. Tandai simpul sebagai dikunjungi dan ulangi sampai semua simpul dikunjungi.

## Kompleksitas
Dengan antrian prioritas (binary heap), kompleksitasnya O((V + E) log V).
