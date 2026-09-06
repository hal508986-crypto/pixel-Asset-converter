# Real t2i Material Source Qualification Study

This study tests whether the quality of the upstream Material Source dominates
the downstream 64x64 Pixel Tile / Tileset / Map result.

## Experimental contract

- Material families: `grass`, `dirt`, `water`, `stone`
- Candidates: 8 independent real t2i outputs per family, 32 total
- Raw images are immutable and retained separately from 1024x1024 normalized working images
- No crop, recolor, noise, brightness adapter, pseudo-candidate, or derived variant was used
- Each candidate receives the same 8 compile variants, palette budget 28, seed 42, and 10x10 Map Probe
- `dirt` is additionally probed through the Road Renderer
- `water` is additionally probed through the River Renderer
- `stone` is additionally probed as an object material
- Blind review assets are generated, but human ratings are not filled in automatically

## Execution

```powershell
$env:PYTHONPATH = "src"
python -m pixel_tile_compiler.cli import-real-material-sources `
  --study e2e/real_t2i_material_qualification `
  --input e2e/real_t2i_material_qualification/incoming `
  --generator "OpenAI ImageGen" `
  --model built-in-imagegen

python -m pixel_tile_compiler.cli qualify-real-material-sources `
  --config experiments/real_t2i_material_qualification.yaml
```

The generation queue and prompt matrix are kept in
`e2e/real_t2i_material_qualification/generation/`. The raw source and its
provenance are kept under each family’s `real_sources/` directory.

## Observed result

All 32 candidates completed the deterministic qualification pipeline. The
source-effect ratio was classified as `Source-dominant` for every family and
overall:

| Material | Top source | Library fitness | Source-effect ratio |
|---|---|---:|---:|
| dirt | `dirt_real_02` | 0.800043 | 0.901587 |
| grass | `grass_real_05` | 0.798761 | 0.803401 |
| stone | `stone_real_05` | 0.792605 | 0.692810 |
| water | `water_real_01` | 0.829516 | 0.922428 |
| overall | — | — | 0.830057 |

Candidate diversity scores were 0.793106–0.816888 by family, and the near-
duplicate gate found no pairs.

The compiler-sensitivity controls also retained the best-source advantage for
all four families. This supports the hypothesis for this fixed probe set, but
does not establish causal or statistically significant evidence.

## Human review boundary

The automatic top two are provisional only. They are copied under
`provisional/` and do not overwrite the accepted Material Library. Human review
must be performed from:

- `e2e/real_t2i_material_qualification/blind_review/blind_review_board.png`
- `e2e/real_t2i_material_qualification/blind_review/blind_review_sheet.csv`

After ratings are entered, import them with:

```powershell
python -m pixel_tile_compiler.cli import-material-review `
  --study e2e/real_t2i_material_qualification `
  --csv e2e/real_t2i_material_qualification/blind_review/blind_review_sheet.csv
```

The current accepted Library remains unchanged. In the visual comparison,
the current stone baseline is visibly a road/landscape-like source rather than
a neutral stone surface; this is an observation for later Library curation,
not an automatic replacement decision.

## Main outputs

- `summary/real_source_ranking.json`
- `summary/variance_decomposition.json`
- `summary/compiler_sensitivity.json`
- `summary/source_downstream_correlations.json`
- `summary/current_vs_real_comparison.png`
- `blind_review/blind_review_board.png`
