# Y-OGG encoder bench

- When: 2026-09-03 15:24 MSK
- Hard cap: 1572864 bytes (1.5 MiB)
- Modes: baseline, new
- Wall time: 29.6 s
- Encoder: libvorbis in OGG (not Opus)
- Author: de1ze1 (Вадим Угаров)

## Strategy

**baseline** — `ffmpeg -ac 1 -ar 16000 -c:a libvorbis -q:a 0` (old always-16 kHz floor).

**old** — 16 kHz integer q binary search filling ≤ 1.5 MiB (valid q −1…8).

**new** — probe duration/rate → target kbps from duration with 15% headroom, capped at ~64 kbps so short tracks are *not* packed to 1.5 MiB → adaptive 44100/32000/22050/16000 → equal-power pan downmix + encoder cutoff → VBR q (>=0), then lower q / rate, last-resort managed CBR. Negative q is never passed as a separate argv token (ffmpeg treats -1 as a flag; that was the Type-03 ~2.75 MB failure).

## Summary

| mode | n | under cap | overflows | median MiB | mean MiB | median kbps | min MiB | max MiB |
|------|---|-----------|-----------|------------|----------|-------------|---------|---------|
| baseline | 18 | 18 | 0 | 0.780 | 0.788 | 26.7 | 0.339 | 1.196 |
| new | 18 | 18 | 0 | 0.899 | 0.928 | 30.5 | 0.756 | 1.243 |


Vs a naive fill of the 1.5 MiB cap (~1.45 MiB typical for the old 16 kHz q-search), **new median is 0.899 MiB (60% of the cap)**. Short tracks spend the leftover budget on sample rate (44.1/32 kHz), not on packing.

## Overflows (over 1.5 MiB)

None. All encoded files in this run are ≤ 1,572,864 bytes.

## Per-file comparison

| file | dur s | genre | bpm | src MiB | base MiB | base Hz | base under | new MiB | new Hz | new mode | new kbps | new under | vs base |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| angelhard - HARD MOVEMENT (Slowed).mp3 | 104.3 | hardcore/gabber | 152.0 | 4.392 | 0.339 | 16000 | yes | 0.871 | 44100 | q=2.0 | 70.1 | yes | +157% |
| Bountyhunter - Woops (Original Remastered Mix).mp3 | 256.9 | hardcore/gabber | 152.0 | 9.804 | 0.835 | 16000 | yes | 0.959 | 22050 | q=0.5 | 31.3 | yes | +15% |
| Carpenter Brut - Disco Zombi Italia.mp3 | 317.4 | synthwave | 123.0 | 13.064 | 1.022 | 16000 | yes | 1.014 | 16000 | q=0.0 | 26.8 | yes | -1% |
| Dazegxd - 4ever (no data).mp3 | 251.5 | breaks/dnb | 83.4 | 5.581 | 0.713 | 16000 | yes | 0.889 | 22050 | q=0.5 | 29.7 | yes | +25% |
| DJ Rob - Boys Interface (Dark Headz Remix).mp3 | 179.3 | hardcore/gabber | 161.5 | 6.978 | 0.582 | 16000 | yes | 0.814 | 22050 | q=2.0 | 38.1 | yes | +40% |
| DJ UNIVXRSEL - DROLLXD.mp3 | 137.1 | hardcore/gabber | 117.5 | 4.972 | 0.458 | 16000 | yes | 0.974 | 32000 | q=2.0 | 59.6 | yes | +113% |
| Kairi The Maid - Crystal of Faded Chilhoods.mp3 | 360.0 | electronic/ambient | 92.3 | 7.510 | 1.099 | 16000 | yes | 1.051 | 16000 | cbr28k | 24.5 | yes | -4% |
| LiterallyMe - Hard Parade.mp3 | 162.9 | hardcore/gabber | 89.1 | 6.309 | 0.530 | 16000 | yes | 0.775 | 22050 | q=2.0 | 39.9 | yes | +46% |
| Lone - Airglow Fires.mp3 | 360.0 | electronic/ambient | 117.5 | 6.820 | 1.081 | 16000 | yes | 1.070 | 16000 | cbr28k | 24.9 | yes | -1% |
| LONOWN, riserayss - worry (ultra slowed).mp3 | 255.6 | electronic/slowed | 92.3 | 5.473 | 0.791 | 16000 | yes | 0.898 | 22050 | q=0.5 | 29.5 | yes | +13% |
| Mick Gordon - Type-03.mp3 | 322.9 | industrial/metal | 89.1 | 6.686 | 1.081 | 16000 | yes | 1.077 | 16000 | q=0.0 | 28.0 | yes | -0% |
| Revolution SS13 Fan-Track.mp3 | 389.7 | electronic/game | 99.4 | 5.951 | 1.196 | 16000 | yes | 1.243 | 16000 | cbr26k | 26.8 | yes | +4% |
| You'll Be UNDER MY WHEELS.mp3 | 237.0 | industrial/rock | 152.0 | 7.168 | 0.769 | 16000 | yes | 0.900 | 22050 | q=0.5 | 31.9 | yes | +17% |
| Бутусов - Тема Дороги (OST Жмурки).mp3 | 329.0 | soundtrack | 107.7 | 12.558 | 0.988 | 16000 | yes | 0.984 | 16000 | q=0.0 | 25.1 | yes | -0% |
| Зверев Сергей - Dolce Gabbana.mp3 | 188.0 | pop/rap | 123.0 | 7.545 | 0.601 | 16000 | yes | 0.756 | 22050 | q=1.0 | 33.7 | yes | +26% |
| Любэ - Возле твой любви (OST Князь Владимир).mp3 | 214.4 | soundtrack | 99.4 | 8.264 | 0.679 | 16000 | yes | 0.856 | 22050 | q=1.0 | 33.5 | yes | +26% |
| Русская Рать - Ой что то мы засиделись братцы.mp3 | 267.8 | folk/rock | 92.3 | 10.219 | 0.833 | 16000 | yes | 0.798 | 16000 | q=0.5 | 25.0 | yes | -4% |
| Серега Пират - Вайбмен.mp3 | 187.7 | rap | 112.3 | 7.658 | 0.588 | 16000 | yes | 0.768 | 22050 | q=1.0 | 34.3 | yes | +31% |

## Notes

- Source MP3 size is **not** used as a quality proxy.
- `vs base` is size delta of **new vs baseline q=0 @ 16 kHz** (negative = smaller).
- Short tracks should keep 32/44.1 kHz; long tracks may drop to 16 kHz + CBR.
- BPM is a simple onset-flux autocorrelation (ballpark, not a DAW click-track).
- Encoded OGGs are not committed; this report is.

## Full rows

| mode | file | dur s | src B | out B | kbps | Hz | enc | bpm | genre | under | notes |
|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline | angelhard - HARD MOVEMENT (Slowed).mp3 | 104.31 | 4604847 | 355777 | 27.3 | 16000 | q=0.0 | 152.0 | hardcore/gabber | yes | baseline: -ac 1 -ar 16000 -c:a libvorbis -q:a 0; baseline 16k q=0 |
| baseline | Bountyhunter - Woops (Original Remastered Mix).mp3 | 256.86 | 10279749 | 875043 | 27.2 | 16000 | q=0.0 | 152.0 | hardcore/gabber | yes | baseline: -ac 1 -ar 16000 -c:a libvorbis -q:a 0; baseline 16k q=0 |
| baseline | Carpenter Brut - Disco Zombi Italia.mp3 | 317.44 | 13698502 | 1071348 | 27.0 | 16000 | q=0.0 | 123.0 | synthwave | yes | baseline: -ac 1 -ar 16000 -c:a libvorbis -q:a 0; baseline 16k q=0 |
| baseline | Dazegxd - 4ever (no data).mp3 | 251.48 | 5852302 | 748129 | 23.8 | 16000 | q=0.0 | 83.4 | breaks/dnb | yes | baseline: -ac 1 -ar 16000 -c:a libvorbis -q:a 0; baseline 16k q=0 |
| baseline | DJ Rob - Boys Interface (Dark Headz Remix).mp3 | 179.29 | 7316655 | 609917 | 27.2 | 16000 | q=0.0 | 161.5 | hardcore/gabber | yes | baseline: -ac 1 -ar 16000 -c:a libvorbis -q:a 0; baseline 16k q=0 |
| baseline | DJ UNIVXRSEL - DROLLXD.mp3 | 137.11 | 5213932 | 480459 | 28.0 | 16000 | q=0.0 | 117.5 | hardcore/gabber | yes | baseline: -ac 1 -ar 16000 -c:a libvorbis -q:a 0; baseline 16k q=0 |
| baseline | Kairi The Maid - Crystal of Faded Chilhoods.mp3 | 360.01 | 7874589 | 1152260 | 25.6 | 16000 | q=0.0 | 92.3 | electronic/ambient | yes | baseline: -ac 1 -ar 16000 -c:a libvorbis -q:a 0; baseline 16k q=0 |
| baseline | LiterallyMe - Hard Parade.mp3 | 162.91 | 6615457 | 555504 | 27.3 | 16000 | q=0.0 | 89.1 | hardcore/gabber | yes | baseline: -ac 1 -ar 16000 -c:a libvorbis -q:a 0; baseline 16k q=0 |
| baseline | Lone - Airglow Fires.mp3 | 360.00 | 7151398 | 1133734 | 25.2 | 16000 | q=0.0 | 117.5 | electronic/ambient | yes | baseline: -ac 1 -ar 16000 -c:a libvorbis -q:a 0; baseline 16k q=0 |
| baseline | LONOWN, riserayss - worry (ultra slowed).mp3 | 255.64 | 5738866 | 829448 | 26.0 | 16000 | q=0.0 | 92.3 | electronic/slowed | yes | baseline: -ac 1 -ar 16000 -c:a libvorbis -q:a 0; baseline 16k q=0 |
| baseline | Mick Gordon - Type-03.mp3 | 322.93 | 7010593 | 1133578 | 28.1 | 16000 | q=0.0 | 89.1 | industrial/metal | yes | baseline: -ac 1 -ar 16000 -c:a libvorbis -q:a 0; baseline 16k q=0 |
| baseline | Revolution SS13 Fan-Track.mp3 | 389.69 | 6239616 | 1253955 | 25.7 | 16000 | q=0.0 | 99.4 | electronic/game | yes | baseline: -ac 1 -ar 16000 -c:a libvorbis -q:a 0; baseline 16k q=0 |
| baseline | You'll Be UNDER MY WHEELS.mp3 | 236.97 | 7515869 | 806095 | 27.2 | 16000 | q=0.0 | 152.0 | industrial/rock | yes | baseline: -ac 1 -ar 16000 -c:a libvorbis -q:a 0; baseline 16k q=0 |
| baseline | Бутусов - Тема Дороги (OST Жмурки).mp3 | 329.04 | 13168028 | 1035812 | 25.2 | 16000 | q=0.0 | 107.7 | soundtrack | yes | baseline: -ac 1 -ar 16000 -c:a libvorbis -q:a 0; baseline 16k q=0 |
| baseline | Зверев Сергей - Dolce Gabbana.mp3 | 187.98 | 7911866 | 629748 | 26.8 | 16000 | q=0.0 | 123.0 | pop/rap | yes | baseline: -ac 1 -ar 16000 -c:a libvorbis -q:a 0; baseline 16k q=0 |
| baseline | Любэ - Возле твой любви (OST Князь Владимир).mp3 | 214.38 | 8665779 | 712487 | 26.6 | 16000 | q=0.0 | 99.4 | soundtrack | yes | baseline: -ac 1 -ar 16000 -c:a libvorbis -q:a 0; baseline 16k q=0 |
| baseline | Русская Рать - Ой что то мы засиделись братцы.mp3 | 267.85 | 10715472 | 873290 | 26.1 | 16000 | q=0.0 | 92.3 | folk/rock | yes | baseline: -ac 1 -ar 16000 -c:a libvorbis -q:a 0; baseline 16k q=0 |
| baseline | Серега Пират - Вайбмен.mp3 | 187.72 | 8030081 | 617008 | 26.3 | 16000 | q=0.0 | 112.3 | rap | yes | baseline: -ac 1 -ar 16000 -c:a libvorbis -q:a 0; baseline 16k q=0 |
| new | angelhard - HARD MOVEMENT (Slowed).mp3 | 104.31 | 4604847 | 913793 | 70.1 | 44100 | q=2.0 | 152.0 | hardcore/gabber | yes | target 64.0 kbps (budget 102.5); I=-11.2 LUFS LRA=7.0 LU |
| new | Bountyhunter - Woops (Original Remastered Mix).mp3 | 256.86 | 10279749 | 1005334 | 31.3 | 22050 | q=0.5 | 152.0 | hardcore/gabber | yes | target 41.6 kbps (budget 41.6); I=-12.0 LUFS LRA=8.0 LU |
| new | Carpenter Brut - Disco Zombi Italia.mp3 | 317.44 | 13698502 | 1063418 | 26.8 | 16000 | q=0.0 | 123.0 | synthwave | yes | target 33.7 kbps (budget 33.7); I=-9.3 LUFS LRA=6.6 LU |
| new | Dazegxd - 4ever (no data).mp3 | 251.48 | 5852302 | 932419 | 29.7 | 22050 | q=0.5 | 83.4 | breaks/dnb | yes | target 42.5 kbps (budget 42.5); I=-12.0 LUFS LRA=13.1 LU |
| new | DJ Rob - Boys Interface (Dark Headz Remix).mp3 | 179.29 | 7316655 | 853853 | 38.1 | 22050 | q=2.0 | 161.5 | hardcore/gabber | yes | target 59.7 kbps (budget 59.7); drop to 22050; I=-10.4 LUFS LRA=11.2 LU |
| new | DJ UNIVXRSEL - DROLLXD.mp3 | 137.11 | 5213932 | 1021029 | 59.6 | 32000 | q=2.0 | 117.5 | hardcore/gabber | yes | target 64.0 kbps (budget 78.0); I=-4.4 LUFS LRA=1.1 LU |
| new | Kairi The Maid - Crystal of Faded Chilhoods.mp3 | 360.01 | 7874589 | 1101886 | 24.5 | 16000 | cbr28k | 92.3 | electronic/ambient | yes | target 29.7 kbps (budget 29.7); cbr; I=-10.7 LUFS LRA=5.8 LU |
| new | LiterallyMe - Hard Parade.mp3 | 162.91 | 6615457 | 812855 | 39.9 | 22050 | q=2.0 | 89.1 | hardcore/gabber | yes | target 64.0 kbps (budget 65.7); drop to 22050; I=-10.0 LUFS LRA=9.9 LU |
| new | Lone - Airglow Fires.mp3 | 360.00 | 7151398 | 1121913 | 24.9 | 16000 | cbr28k | 117.5 | electronic/ambient | yes | target 29.7 kbps (budget 29.7); cbr; I=-15.3 LUFS LRA=7.5 LU |
| new | LONOWN, riserayss - worry (ultra slowed).mp3 | 255.64 | 5738866 | 941357 | 29.5 | 22050 | q=0.5 | 92.3 | electronic/slowed | yes | target 41.8 kbps (budget 41.8); I=-10.6 LUFS LRA=9.1 LU |
| new | Mick Gordon - Type-03.mp3 | 322.93 | 7010593 | 1129608 | 28.0 | 16000 | q=0.0 | 89.1 | industrial/metal | yes | target 33.1 kbps (budget 33.1); I=-10.5 LUFS LRA=12.7 LU |
| new | Revolution SS13 Fan-Track.mp3 | 389.69 | 6239616 | 1303775 | 26.8 | 16000 | cbr26k | 99.4 | electronic/game | yes | target 27.4 kbps (budget 27.4); cbr; I=-14.2 LUFS LRA=7.1 LU |
| new | You'll Be UNDER MY WHEELS.mp3 | 236.97 | 7515869 | 943849 | 31.9 | 22050 | q=0.5 | 152.0 | industrial/rock | yes | target 45.1 kbps (budget 45.1); I=-11.2 LUFS LRA=2.8 LU |
| new | Бутусов - Тема Дороги (OST Жмурки).mp3 | 329.04 | 13168028 | 1032028 | 25.1 | 16000 | q=0.0 | 107.7 | soundtrack | yes | target 32.5 kbps (budget 32.5); I=-14.1 LUFS LRA=6.1 LU |
| new | Зверев Сергей - Dolce Gabbana.mp3 | 187.98 | 7911866 | 792653 | 33.7 | 22050 | q=1.0 | 123.0 | pop/rap | yes | target 56.9 kbps (budget 56.9); drop to 22050; I=-14.5 LUFS LRA=5.6 LU |
| new | Любэ - Возле твой любви (OST Князь Владимир).mp3 | 214.38 | 8665779 | 897426 | 33.5 | 22050 | q=1.0 | 99.4 | soundtrack | yes | target 49.9 kbps (budget 49.9); drop to 22050; I=-12.8 LUFS LRA=9.1 LU |
| new | Русская Рать - Ой что то мы засиделись братцы.mp3 | 267.85 | 10715472 | 837128 | 25.0 | 16000 | q=0.5 | 92.3 | folk/rock | yes | target 39.9 kbps (budget 39.9); drop to 16000; I=-13.5 LUFS LRA=2.4 LU |
| new | Серега Пират - Вайбмен.mp3 | 187.72 | 8030081 | 805599 | 34.3 | 22050 | q=1.0 | 112.3 | rap | yes | target 57.0 kbps (budget 57.0); drop to 22050; I=-14.2 LUFS LRA=4.1 LU |
