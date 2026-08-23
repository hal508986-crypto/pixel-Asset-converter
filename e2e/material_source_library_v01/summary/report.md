# Material Source Library v0.1 Summary

Source gate and downstream probes are kept as separate signals.

## grass
- Top: `grass_v01_uniform_dark` (0.8200)
- Worst: `grass_v07_dense_tufts` (0.7768)
- Promoted: `grass_v01_uniform_dark, grass_v03_uniform_light`

## dirt
- Top: `dirt_v08_illustrative` (0.7883)
- Worst: `dirt_v05_dry_variation` (0.7821)
- Promoted: `dirt_v08_illustrative, dirt_v02_fine_warm`

## water
- Top: `water_v01_calm_uniform` (0.7447)
- Worst: `water_v06_dark_depth` (0.7388)
- Promoted: `water_v01_calm_uniform, water_v02_calm_blue`

## stone
- Top: `stone_v01_fine_grain` (0.7362)
- Worst: `stone_v07_large_cracks` (0.7046)
- Promoted: `stone_v01_fine_grain, stone_v08_illustrative`

## Interpretation
- Stationary, moderate-scale material fields tend to be safer library candidates than focal or macro-illustrative sources.
- Compile and map fitness are downstream probes, not substitutes for human review.
- Correlations are exploratory with n=8 per material and should not be treated as causal evidence.

## Correlations
```json
{
  "grass": {
    "n": 8,
    "interpretation": "exploratory",
    "correlations": {
      "stationarity_score": 0.14583,
      "brightness_spatial_variance": -0.066061,
      "color_spatial_variance": 0.280655,
      "texture_density_variance": -0.07382,
      "center_dominance_score": 0.248374,
      "low_frequency_pattern_strength": -0.177662,
      "autocorrelation_peak_risk": -0.051694,
      "orientation_bias": -0.337778,
      "edge_density": -0.750735,
      "estimated_feature_scale_px": 0.228173
    }
  },
  "dirt": {
    "n": 8,
    "interpretation": "exploratory",
    "correlations": {
      "stationarity_score": 0.310719,
      "brightness_spatial_variance": -0.08251,
      "color_spatial_variance": -0.334681,
      "texture_density_variance": -0.237144,
      "center_dominance_score": -0.352627,
      "low_frequency_pattern_strength": -0.288523,
      "autocorrelation_peak_risk": -0.110822,
      "orientation_bias": -0.064234,
      "edge_density": null,
      "estimated_feature_scale_px": -0.426086
    }
  },
  "water": {
    "n": 8,
    "interpretation": "exploratory",
    "correlations": {
      "stationarity_score": -0.226925,
      "brightness_spatial_variance": 0.434988,
      "color_spatial_variance": -0.626385,
      "texture_density_variance": -0.036259,
      "center_dominance_score": -0.604898,
      "low_frequency_pattern_strength": 0.438836,
      "autocorrelation_peak_risk": 0.693379,
      "orientation_bias": 0.117692,
      "edge_density": -0.650575,
      "estimated_feature_scale_px": 0.786529
    }
  },
  "stone": {
    "n": 8,
    "interpretation": "exploratory",
    "correlations": {
      "stationarity_score": 0.725695,
      "brightness_spatial_variance": -0.838507,
      "color_spatial_variance": -0.914022,
      "texture_density_variance": -0.613072,
      "center_dominance_score": -0.791592,
      "low_frequency_pattern_strength": -0.12691,
      "autocorrelation_peak_risk": 0.462816,
      "orientation_bias": -0.393046,
      "edge_density": -0.852127,
      "estimated_feature_scale_px": 0.566081
    }
  }
}
```
