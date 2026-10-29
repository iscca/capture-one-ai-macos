# Detailed reference matching: tones, gray casts and fixed details

Focus on one selected photo. The current product direction defers batch work and prioritizes
accurate reference interpretation. These instructions do not authorize batch editing, final export,
presets/LUT import, screenshot color picking, or RAW/Session sidecar writes.

## Read the reference photograph

Actually view the supplied reference and current C1 render before choosing adjustments.
Explain the look through these photographic relationships:

1. Tone structure: black floor, dark-detail density, subject midtones, white level, highlight rolloff.
2. Gray casts by brightness: whether dark/middle/light nominally gray materials lean green/magenta,
   blue/yellow or remain relatively neutral; whether those tendencies change with lightness.
3. Color mapping: how skin, foliage, sky, product colors and important materials differ in hue,
   chroma and brightness. Preserve credible light direction and material appearance.
4. Fixed details: small, stable corresponding regions that can be revisited after each round.

Gray does not mean RGB channels must be equal. A white wall in warm light or gray shadow
may intentionally carry color. Match the reference relationship instead of whitening everything.
Do not infer white-balance error from an overall histogram or every low-saturation pixel.
Separate an intentional warm/cool split from a uniform cast, and separate either from objects'
intrinsic colors. A styled low-saturation colored object is not automatically a neutral gray target.

## Choose anchored detail pairs

Usually choose a few relevant anchors rather than covering the whole image:

- If present, diffuse nominally neutral materials at different lightness levels; identify their material
  and lighting so their neutrality remains an explicit assumption.
- Skin in comparable illumination, excluding specular shine, deep shadow, lips and makeup unless
  those are the specific goal. Different subjects/complexions need relationship matching, not identical skin RGB.
- Stable foliage/sky/product/material patches, avoiding overlapping colors and edges.

Each reference and target patch is selected separately from the actual images. Cross-scene
correspondence is semantic, not the same screen position. Avoid clipped highlights/near-black
regions as cast evidence. Prefer representative patches to single pixels; inspect mixed-color,
texture and near-black/white warnings and reselect before drawing strong conclusions.

## Measurement through anchor_analysis

Check for `anchor_analysis` in context.editing_tasks.features. The tool writes only a local
task record, not C1 parameters. It requires a current actual-render checkpoint and one reference
already named in the brief. Supply either a same-task `reference_checkpoint_id`, or the explicit
user-provided `reference_path` and file `reference_sha256`. Do not scan personal folders or
guess file paths to obtain a sample. If an attachment is visible only to the vision model and
has no readable image file, give a qualitative analysis and disclose that numeric patch
measurement is unavailable. Do not invent Lab/RGB values or DeltaE.

Payload:

```json
{
  "checkpoint_id": "actual-current-checkpoint",
  "reference_id": "brief-reference-id",
  "reference_path": "explicit-local-image-path",
  "reference_sha256": "actual-file-hash",
  "anchors": [{
    "id": "light-gray-surface", "role": "diffuse light wall under comparable lighting", "kind": "neutral",
    "reference_box": [0.2, 0.2, 0.3, 0.3], "target_box": [0.4, 0.3, 0.5, 0.4]
  }]
}
```

Example coordinates are illustrative, not a recipe. Boxes use EXIF-oriented image bounds
normalized to 0..1, never Capture One RAW coordinates or mask positions. Up to twelve
anchors with kind=neutral/color. Profiles are converted to a common sRGB space before Lab D50
measurement. An untagged RGB/gray image requires explicit `assume_srgb:{reference:true,target:true}`
only for the sides known to be sRGB. Invalid ICC profiles are refused, never silently ignored.
Unknown color space, reference compression, and different lighting limit comparison accuracy.

Read the report as evidence:

- Five L* bands and their occupancy describe tone distribution. Their a/b values depend on scene
  content. `low_chroma_candidates` is a heuristic subset, not identification of gray materials.
- Each patch provides median sRGB, median Lab, chroma, hue when meaningful, and variability flags.
- `reference_minus_target` gives brightness/chroma/opponent-axis differences; positive a points
  toward red/magenta, positive b toward yellow. The patch's neutral goal remains the reference cast.
- DeltaE76 is a descriptive rendered-patch difference, not a style similarity percentage or a promise
  of perceptual correctness. Low-chroma hue is deliberately undefined.
- Neutral tone pairs are not curve coordinates. A tone-order conflict can indicate unmatched
  lighting/materials or need for local work; never force a global nonmonotonic curve from them.

## Translate differences into C1 decisions

First decide which relationship is wrong. Use runtime capabilities, fresh reads and absolute targets:

| Evidence plus visual judgment | Candidate approach |
|---|---|
| Important neutral materials share a broadly consistent cast difference across tones | Consider white balance, while checking intentional colored light |
| Shadows/midtones/highlights need different cast relationships | Consider color balance or channel curves; protect material/skin colors |
| Black density or highlight rolloff differs | Consider exposure/HDR, levels or Luma/RGB curves as appropriate |
| A specific color differs in hue/chroma/lightness | Read Basic/Advanced color mixer; choose an observed range and inspect spillover |
| A needed correction conflicts with a protected region | Narrow the correction; use qualified local tools only when actually necessary and allowed |

The measurement does not derive C1 slider values, kelvin, Device RGB selections, or a LUT.
Preview sRGB is not the native Advanced mixer picker space. Do not submit sampled reference
RGB as a supposedly exact native color-range selection, or copy Lab coordinates into curves/wheels.
Start with modest changes for a clear purpose; do not exhaustively sweep parameters.

Save a P1 reference_plan linked to the current `anchor_analysis_id`, keeping observation,
uncertainty, corresponding region, direction, priority and limitations. Follow
[photographic-workflow.md](photographic-workflow.md) for feedback and approved details.

## Recheck the same details

After adjust/readback, obtain a new actual render and checkpoint. View it and verify that each
patch still covers the same feature. Then reuse:
`{checkpoint_id,baseline_analysis_id,alignment_confirmed:true}`.
This keeps the original reference hash and both sets of boxes. Do not move patches solely to
obtain a smaller difference. If geometry/materials changed, make a deliberate new analysis
and explain why the old measurements are no longer directly comparable.

Recheck protected details as well as the intended target. A smaller foliage DeltaE is not success
if skin or product colors deteriorate. Rendered metrics support, rather than replace, the
P1 target/protection/feedback visual review and the photographer's final acceptance.

Describe the outcome in photographic language: tonal density, cast by brightness, main color
relationships, fixed details that improved, and limits caused by light/material/content differences.
Do not claim camera calibration, exact film response, print accuracy or full-resolution detail verification.
