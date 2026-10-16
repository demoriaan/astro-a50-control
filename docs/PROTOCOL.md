# ASTRO A50 Gen 4 – HID protocol

Recovered from `AstroCommandCenter.exe` (ASTRO Command Center MSI, built 2021-05-05,
Qt 5 / MSVC, x86) with Ghidra via REA, then checked against a live base station
`9886:002c`, firmware 40372 (built 2022-04-20), DSP 43.

ACC contains three protocol families: **Mimas** and **Europa** (both A50 base stations,
shared code in `AstroA50Sub`) and **Kuiper** (A20 transmitter). The tested base station
speaks the **Mimas** dialect: it rejects the Europa-only "default mode" read (`0x6b`)
with `HID_ERROR_UNHANDLED_PACKET`, and its noise gate response is the 2-byte Mimas form.
This document only covers what the tested device accepts.

## Transport

* HID interface 6, vendor usage page `0xFF32`, usage `0x74`; report ID `2`,
  63-byte input report (usage `0x75`) and 63-byte output report (usage `0x76`).
  Linux: plain `read()/write()` on `/dev/hidrawN`, 64 bytes including the report ID.
* Request: `02 | cmd | seq<<6 | len | payload…` (zero padded).
  `seq` is a 2-bit counter; ACC increments it per request (`FUN_004794f0`).
  Commands with bit 7 set are relayed to the headset and get an extra `0x0a` in front
  of the payload (`len` includes it).
* Response: `02 | status | seq<<6 | len | data…`. Status `0x02` = OK. Status `0x00` with
  no data also counts as OK (brightness setter). Status `0x01` = error; data is a
  little-endian u32 code plus a NUL-terminated string, e.g. `01000000 "HID_ERROR_UNHANDLED_PACKET"`,
  `01000001 "HID_ERROR_SLAVE_TIMEOUT"`.
* Every open hidraw descriptor receives every input report. Two clients therefore see
  each other's responses (observed: a status read picked up another process' answer).
  The app takes an exclusive `flock` on the node.
* Most setters echo `[cmd, first params…]` (ACC retries up to 5 times until the echo
  matches, `FUN_00490690`).

## Values with two copies

Every setting has a **live** value and a value **saved** on the headset. Setters change
the live copy only; `0x61` (ACC log: "sync") stores everything. ACC's "revert" writes
the saved copy back. Verified: setting sidetone to 10 read back as `0a 00` (live, saved);
auto power-off 20, brightness 60 and mic EQ 1 read back differently with `[0]` and `[1]`
(ACC's Mimas code reads those three without a parameter, i.e. only the live copy).

## Commands

| cmd | name | request payload | response data | verified |
|-----|------|-----------------|---------------|----------|
| `03` | base info | – | 38 bytes; `[4:6]` VID, `[6:8]` PID, `[8:15]` build date (u16 year, month, day, h, m, s), `[0x15:0x19]` u32 firmware version | read |
| `54` | headset status | – | `[bits]`: bit0 docked, bit1 switched on (ACC `FUN_0048f070`) | yes |
| `55` | base firmware minor | – | `[minor]`; ACC shows `major.minor`, e.g. 40372.43 | read |
| `61` | save ("sync") | – | `[61, code]`; 0 = ok, 2 = busy → retry after 1.5 s. Takes ~2 s | yes |
| `62` | set slider | `[slider, 0-100]` | `[62, slider]` | yes |
| `63` | set EQ gains | `[preset 1-3, g1..g5]`, g = dB + 12 | `[63, preset]` | yes (GUI) |
| `64` | set noise gate | `[mode]` | `[mode]` (echoes the value, not the command) | yes |
| `67` | set active EQ preset | `[preset 1-3]` | `[67, preset]` | yes |
| `68` | get slider | `[slider]` | `[68, slider, live, saved]` | yes |
| `69` | get EQ gains | `[preset]` | `[69, preset, 5×live, 5×saved]`, dB + 12 | yes |
| `6a` | get noise gate | – | `[6a, live, saved]` | yes |
| `6c` | get active EQ preset | – | `[preset 1-3]` | yes |
| `6d` | set EQ name | `[preset, n+1, name(n), 00]`, n ≤ 49 | `[6d, preset]` | yes (same name) |
| `6e` | get EQ name | `[preset, 0=live / 1=saved]` | `[6e, preset, name…\0]` | yes |
| `6f` | set EQ band | `[preset, band 1-5, bw u16le, freq u16le]` | `[6f, preset, band]` | yes (GUI) |
| `70` | get EQ band | `[preset, band]` | `[70, preset, band, bw live, bw saved, freq live, freq saved]` (u16le each) | yes |
| `71` | set mic EQ | `[mode]` | `[71]` | yes (same value) |
| `72` | get live game/voice mix | – | `[0-255]` | yes |
| `73` | set default balance | `[0-255]` | `[73]` | yes (same value) |
| `74` | set auto power-off | `[minutes]` (10/15/20/25/30) | `[74]` | yes (same value) |
| `75` | set base LED brightness | 9 × `[20-70]` | status 0, no data | yes (same value) |
| `76` | set alert volume | `[0-100]` | `[76]` | yes (same value) |
| `77` | get default balance | `[0=live / 1=saved]` | `[0-255]` | yes |
| `78` | get auto power-off | `[0=live / 1=saved]` (none = live) | `[minutes]` | yes |
| `79` | get brightness | `[0=live / 1=saved]` (none = live) | 9 bytes, ACC averages them | yes |
| `7a` | get alert volume | `[0=live / 1=saved]` | `[0-100]` | yes |
| `7b` | get mic EQ | `[0=live / 1=saved]` (none = live) | `[mode]` | yes |
| `7c` | get battery | – | `[percent \| 0x80 if charging]` | yes |
| `d6` | headset firmware minor (relayed) | – | `[minor]` | read |
| `da` | headset firmware major (relayed) | – | u32le, e.g. 39964 | read |
| `83` | headset info (relayed) | – | answered `HID_ERROR_SLAVE_TIMEOUT` with the headset switched on; ACC then falls back to `da` (`FUN_00484280`) | **not working** |

### Enumerations

* Slider IDs: 0 stream mic, 1 stream chat, 2 stream game, 3 stream AUX, 4 USB mic level,
  5 sidetone (ACC properties `0x29…0x26`, `0x24`, `0x25`; INI keys `Microphone_Value`,
  `Chat_Audio_Value`, `Game_Audio_Value`, `Aux_In_Value`, `Mic_Output_Value`, `Sidetone_Value`).
* Noise gate (raw): 0 Streaming, 1 Night, 2 Home (ACC default), 3 Tournament
  (mapping from `FUN_00489c30`/`FUN_00487940` plus the enum string table at `0x425710`).
* Mic EQ: 0 Bright, 1 Airy, 2 Warm.
* Balance / mix: **0 = all voice/chat, 255 = all game.** Verified twice: the headset's
  balance buttons ("game" raised 159 → 255, "voice" lowered 255 → 205), and independently of
  button labels by playing a tone only on the `stereo-chat` sink and turning the balance until
  it was quietest → 255. Note: eh-fifty documents the opposite (0 = all game).
* EQ: band 1 and 5 have no bandwidth (shelves, bandwidth 0); bands 2–4 bandwidth is
  Q12 fixed point (`raw / 4096`), ACC range 0.1–3.0. Frequency 80–15000 Hz, gain ±12 dB.
  ACC labels the value "BANDWIDTH"; whether the DSP treats it as octaves or Q is not known.

## Cross-check with eh-fifty

[eh-fifty](https://github.com/tdryer/eh-fifty) (independently reverse-engineered PyUSB library, used by
[astro-a50-gui](https://github.com/OJambrina/astro-a50-gui)) lists the same command codes,
slider IDs and noise gate values as recovered here. Differences: it lacks LED brightness and
auto power-off (`74/75/78/79`), sends no `0x0A` prefix for relayed commands (it passes it as
payload instead – same bytes on the wire), and its balance direction is reversed (see above).

## Not covered / unknown

* Firmware update (`0x83` data chunks, DFU reset) – deliberately not implemented.
* Europa variant (12-byte noise gate blob, `0x6b` default mode, `0x65` set default mode).
* Unsolicited input reports: none observed during testing; ACC polls.
