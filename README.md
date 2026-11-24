# Global Operations (2002) LithTech ABC v108 Converter

**Released by [nibsly / nibs](https://github.com)** *(built with Claude)*

A standalone, zero-dependency Python batch converter that transforms **Global Operations (2002)** LithTech `.abc` (v108) models into **Source Engine `.smd` meshes, animations, and `.qc` compile scripts**.

---

## Features
* **Zero External Dependencies:** Runs using only Python 3's built-in standard library.
* **Full Rig & Animation Support:** Exports bone hierarchies, vertex weights (with automatic Source 3-bone link capping), UVs, sockets (`$attachment`), and keyframe animations resampled to your target FPS.
* **Smart Viewmodel Grouping:** Automatically separates always-visible weapon bodies, optional attachments (e.g., `silencer`, `handle`, `clip1`), and alternate hand mesh variants (`b`, `c`, `d`) into Source `$bodygroup` blocks.
* **Batch Processing & Safety:** Processes entire folders at once, logs a `batch_report.txt`, and protects non-tool directories from accidental overwrites.
* **Coordinate Space & Mirroring:** Automatically converts LithTech's left-handed coordinate system into Source's right-handed space, with optional `--mirror` flags for viewmodel handedness.

---

## Requirements
* **Python 3.7+**
* *(Optional)* **[Blender Source Tools](http://steamreview.org)** if you want to import the `.smd` files into Blender to edit or re-export as `.fbx` / `.obj`.
* *(Optional)* **Crowbar / StudioMDL** if you are compiling the generated `.qc` files directly for Source Engine games (e.g., Counter-Strike: Source, Garry's Mod).

---

## Quick Start

### 1. Inspect an `.abc` file (Dry Run)
List the bones, pieces, hand variants, sockets, and animations inside a file without exporting anything:
```bash
python globops_abc_batch.py "V_M4CARBINE.ABC" --info
```

### 2. Convert a Single Model
```bash
python globops_abc_batch.py "V_M4CARBINE.ABC" ./output
```

### 3. Batch Convert an Entire Folder
By default, the script filters for viewmodels (`V_*.abc`). To convert **all** `.abc` files in a folder (including player models or world props), pass `--pattern "*.abc"`:
```bash
python globops_abc_batch.py "C:\GlobOps\models" ./output --pattern "*.abc" --recursive
```

---

## Output Structure
Each converted model gets its own self-contained folder (`<output>/<model_name>/`):
```text
output/
└── v_m4carbine/
    ├── v_m4carbine.qc      # Ready-to-compile Source QC script
    ├── body.smd            # Main weapon mesh and skeleton
    ├── parts/              # Optional bodygroups (silencer, handle, alternate hands)
    └── anims/              # Individual animation SMDs (idle, fire, reload, etc.)
```

---

## Useful CLI Options

| Flag | Description |
| :--- | :--- |
| `--pattern "*.abc"` | Change filename filter (defaults to `V_*.abc`). |
| `--recursive` | Search subfolders recursively. |
| `--scale 0.5` | Scale multiplier from LithTech units to Source units (default `0.5`). |
| `--fps 30` | Target framerate for resampled animations (default `30`). |
| `--hands d` | Default hand mesh variant (`b`, `c`, or `d`). |
| `--mirror` | Mirror all models left/right (useful if `cl_righthand` is inverted in-game). |
| `--mirror-only v_anaconda` | Mirror only specific comma-separated models. |
| `--on handle` | Force hidden-by-default pieces (`handle`, `silencer`, `clip1`) to be visible. |
| `--mat 0=m4_tex` | Map numeric material IDs to custom material names. |
| `--no-flip-v` | Disable vertical UV coordinate flipping. |

---

## Known Limitations & Notes
* **LithTech Version:** Strictly validates and reads **ABC v108** files used by *Global Operations* (2002).
* **LODs:** Currently extracts **LOD 0** (highest detail mesh).
* **QC Activities:** `$sequence` activity mappings (`ACT_VM_IDLE`, `ACT_VM_PRIMARYATTACK`, etc.) are auto-guessed from animation names and should be verified against your target weapon script.
* **Non-Viewmodels:** If converting world models (`W_*.abc`) or character models, you may want to pass `--no-origin` so the viewmodel camera offset isn't baked into the `.qc`.

---

## Credits & License
* Created and released by **[nibsly / nibs](https://github.com)** with reverse-engineering and code generation assistance from Claude.
* Built for the *Global Operations* (2002) modding community.
* Licensed under the **MIT License** — free to use, modify, and build upon with attribution.
