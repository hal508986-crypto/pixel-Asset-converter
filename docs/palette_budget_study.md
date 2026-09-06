# Palette Budget & Ramp Structure Study

The first, deliberately small, round tests palette freedom while freezing the
upstream Real t2i Source.

## Fixed inputs

| Material | Frozen Source |
|---|---|
| grass | `grass_real_05` |
| dirt | `dirt_real_02` |
| water | `water_real_01` |
| stone | `stone_real_05` |

The source manifest records both the raw-source SHA-256 from the previous Real
t2i qualification and the normalized-source SHA-256 used by this study.

## First round

- 4 materials
- 60 conditions
- 8 deterministic compile variants per condition
- 480 Single Tile outputs
- Palette budgets: `true_color`, 64, 32, 24, 16, 12, 8
- Modes: `global_free`, `per_material`, `semantic_ramp`
- Dithering: off
- Representative 10x10 Map Probes: `global_free/24` and `semantic_ramp/16`
- Representative semantic probes: `global_free/24` for Dirt/Road, Water/River, and Stone/Object

`per_material` and `global_free` are intentionally both present, but their
outputs are expected to be indistinguishable in this Single Material Tile
round. Their meaningful comparison requires a mixed-material MAP palette.

## Observed provisional ranking

| Material | Best condition | Pixel-art fitness |
|---|---|---:|
| dirt | `semantic_ramp/8` | 0.362179 |
| grass | `global_free/true_color` | 0.521189 |
| stone | `semantic_ramp/8` | 0.546444 |
| water | `semantic_ramp/8` | 0.560635 |

These are automatic exploratory rankings, not acceptance decisions. In
particular, the current metrics show that very small Semantic Ramps often form
large coherent clusters, so `cluster_coherence_score` must be read together
with `material_identity_score`, `semantic_readability_score`, and human review.

The provisional result does not support a universal “fewer colors is better”
claim. Grass currently favors the unquantized control, while the other three
materials favor an 8-color Semantic Ramp under the current heuristic score.

The representative semantic probes completed successfully. Dirt/Road retained
full graph fidelity and zero road break risk; Water/River retained full graph
and flow fidelity with a river break risk of 0.007552. These are renderer
contract checks for the 24-color Global Free condition, not evidence that the
8-color Semantic Ramp is safe for those renderers.

## Outputs

- [study output](E:/ena-dri/repos/pixelart-compiler/e2e/palette_budget_study)
- [comparison board](E:/ena-dri/repos/pixelart-compiler/e2e/palette_budget_study/summary/comparison_board.png)
- [condition ranking](E:/ena-dri/repos/pixelart-compiler/e2e/palette_budget_study/summary/condition_ranking.json)
- [palette effect](E:/ena-dri/repos/pixelart-compiler/e2e/palette_budget_study/summary/palette_effect.json)
- [blind review board](E:/ena-dri/repos/pixelart-compiler/e2e/palette_budget_study/blind_review/board.png)

Run with:

```powershell
$env:PYTHONPATH = "src"
python -m pixel_tile_compiler.cli study-palette-budget `
  --config experiments/palette_budget_study.yaml
```

After entering ratings into `blind_review/review.csv`:

```powershell
python -m pixel_tile_compiler.cli import-palette-review `
  --study e2e/palette_budget_study `
  --csv e2e/palette_budget_study/blind_review/review.csv
```

Import updates the condition records only. It never promotes a profile or
changes the Material Library.

Automatic palette profiles remain provisional and are not written to the
Material Library.
