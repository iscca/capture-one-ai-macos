# Photographic tasks and reference matching (Mac P0 / P1)

## Brief and first use

Use `operations(action=task, task_action=diagnose)` to inspect connection, single selection,
Session eligibility, candidate permission, preview permission and pending operations.
It is read-only and grants no permission. Confirm that this client can display image blocks.
Explain once that temporary JPEG rendering and cloud model transmission are separate:
local bridge execution does not guarantee that images stay on-device in a cloud vision client.
Do not repeat authorization already given for this Session.

Use the current selected version as the starting point. It may already contain the
photographer's work; do not call it an untouched RAW or automatically reset it.
Infer a concise brief from the request and picture. Ask only about choices that
materially change the result or conflicts such as exact product color versus strong tint.
State a short visual plan and proceed when intent is clear.

Persist the brief with fresh `read(fields=[])` target/token and a new request ID:

```json
{
  "command": "operations", "action": "task", "task_action": "start",
  "payload": {
    "mode": "optimize", "goals": ["Open facial midtones while preserving sunset atmosphere"],
    "protections": ["Keep clothing color and warm light"], "subject": "portrait",
    "strength": "light", "acceptance": ["Natural face, readable sky highlights"],
    "allowed_local": true, "starting_point": "current"
  }
}
```

Examples omit real target/token/request_id; obtain them from current observations, never
copy placeholders. Mutation responses place the saved task under `result.task`; read
`task_action=get, task_id=...` to inspect it later. `context` lists relevant task summaries.
Before resuming, inspect the task, current target and pending operations.

Photographic priorities:

| Subject | Check | Avoid |
|---|---|---|
| Portrait | Skin transitions, facial modeling, separation | Making every face pale or removing intentional colored light |
| Product | Important colors, packaging text, white surfaces, material | Tinting brand colors or flattening reflective highlights |
| Landscape/architecture | Light direction, depth, sky/ground relationship | Raising all shadows or creating halos |
| Night/stage | Lighting color, atmosphere, subject visibility | Neutralizing intentional colored sources |
| Monochrome | Tonal separation and highlight/shadow transitions | Only removing saturation |

Explicit user intent takes precedence over these defaults. Usage informs judgment;
it does not authorize output changes, print/HDR validation or final export.

## Reference matching

For references, use `mode=reference_match` with `references` entries containing
`id` (client attachment identifier), `role` (what to borrow), and `kind` (`same_scene`
or `cross_scene`). References remain client-provided; the bridge does not fetch
images from these IDs. Actually inspect each reference in the vision client.

Save `reference_analysis` text fields: `tone`, `color`, `texture`, `limitations`,
`priority`. Analyze subject brightness, highlight rolloff, shadow density, black floor,
overall/light-region color biases, saturation distribution and key object colors.
Identify lighting, haze, depth of field, makeup or materials that grading cannot recreate.
Do not imply a measured film emulation, apply another photo's absolute settings blindly,
average unrelated reference colors, or invent a similarity percentage.

Same-scene references can support closer corresponding-region comparisons. Cross-scene
references transfer relationships while retaining plausible light and important target colors.
Compare semantic counterparts (skin, sky, vegetation, neutral packaging) only when present.
Several references need explicit roles; clarify contradictory goals when necessary.

Strength is `light` (borrow key traits), `close` (clear style match), or `maximum`
(most feasible match within agreed constraints), never one multiplier for all controls.
Default priority: explicit request, protected details/colors, plausible light/material,
then reference traits. Only change priority when the user requests that tradeoff.

## Candidate and render-backed rounds

1. Start the task, obtain an authorized actual render, then save `checkpoint`
   with `{preview_request_id, label:"起始效果"}`. A checkpoint captures the render and
   selected-layer color modules, verifies its script-exposed revision and keeps a private
   copy beyond the ordinary eight-image cache. Capture is read-only toward C1.
2. Create one registered candidate using `images(action=create_candidate)`; bind it using
   `task_action=bind` with `{candidate_request_id}`. Continue the task on that candidate.
   To continue an already bound task reuse it; to create another direction start a new task.
3. Choose only modules needed for visible goals. Use normal read → adjust → readback →
   actual render. Global first, local only if required and `allowed_local` permits it.
4. Save `checkpoint` with the new preview request ID. The current selected layer determines
   the snapshot scope; a full-image render does not imply every layer was snapshotted.
5. Use `compare` with `{roles:["initial","current"]}` or `["previous","current"]`.
   MCP returns labelled images; CLI returns retained paths that must actually be opened.
   For reference work show the client reference alongside the current actual render.
6. Call `review` with `{checkpoint_id,seen_sha256,improved,assessment}` only after seeing
   the image. `assessment` contains text for `goal`, `protections`, `light_color`, `edges`,
   `remaining`; reference tasks additionally require `reference`. On a P1 intent checkpoint also
   submit the itemized results described below. Use the original artifact
   hash in `seen_sha256`, even if MCP supplies a display proxy. This is a client declaration,
   not independently proven vision. Do not approve unseen previews.

State concise progress: reading the photo, adjusting, rendering, checking the result;
for unknown operations, checking the previous operation. No user-facing technical IDs
are needed unless troubleshooting.

## Feedback, approval, stopping

Persist user feedback with `feedback` payload `{kind,message,protect:[...]}`.
Kinds: `brightness`, `temperature`, `strength`, `subject`, `background`,
`prefer_previous`, `custom`. Translate the request into a targeted correction; do not
reset the whole grade or change unrelated areas. Preserve earlier protections unless
the user explicitly revises them using `update` (brief fields merged by name).

Example: "Face is good; make greens quieter, keep brightness" records the face and
brightness as protections while adjusting green appearance. Protection is a planning
and visual-review constraint, not a pixel mask lock or a guaranteed unchanged skin color.
When a global change would violate it, narrow the adjustment or explain the conflict.

After two reviewed checkpoints without improvement the task enters `needs_direction`.
Stop automatic trials, show the best available reviewed version and ask for direction.
Baseline inspection is not a failed improvement round; only review completed edit rounds.
User feedback can resume the task. Stop on achieved goals; do not add grain/curves merely
to use tools. A user's stop invokes `stop`, which does not require a current C1 selection.

Only invoke `accept` with `{checkpoint_id,user_confirmation}` after the user actually
accepts the current reviewed version. Do not invent that confirmation. AI review,
execution confirmation and user approval are separate states.

## Deliberate restore and manual handoff

`restore` with `{checkpoint_id}` writes saved color parameters into the bound candidate
through the same guarded `adjust` route. Read fresh target/token first. The initial source
checkpoint can be pasted into the candidate; never restore into the protected source.
The selected layer must match and the saved layer inventory/settings must still match.
It does not recreate/remove layers, restore masks, hidden Skin parameters, unrelated
adjustments or adjustments on other layers. After local work this can be a partial
image restore; communicate the scope before executing a requested return to a version.

After restoration obtain a new render, checkpoint and review. `parameters_restored`
alone never means the earlier full image was restored. No transaction is promised.
On partial/unknown restore query its original request ID and any saved child IDs;
do not repeat the restore under a new ID. Parent receipts preserve child requests.

After manual C1 edits, reread all affected modules and render again. Stale checkpoint
review/accept is rejected. Preserve deliberate photographer changes; do not paste an
old snapshot over them unless the user explicitly requested restoration.
Low-resolution previews do not verify original-size grain/detail, print color or RAW clipping.

## P1: reference traits, targeted feedback and partial recognition

Check `context.editing_tasks.features`; older bridges/tasks do not imply P1 records exist.
The bridge records declarations and validates version relationships, not photographic accuracy.

After actually seeing the current checkpoint and reference, save `reference_plan` with
`{checkpoint_id,targets:[...]}`. Each target has:

- `id`: unique within this plan; `reference_id`: one of the current brief's reference IDs.
- `observation`: what is visible in the reference; `uncertainty`: what cannot be confidently compared.
- `region`: semantic counterpart on the target photo, not pixel coordinates or a mask guarantee.
- `direction`: intended change or relationship; `protect`: explicit aspects to preserve.
- `priority`: 1..5, with 1 highest; `limitation`: lighting/content/tool constraints, or an explicit absence of known constraints.

Use a small number of consequential traits; the interface allows up to twelve to avoid
unbounded analysis. For cross-scene references, compare corresponding semantic regions
only where they exist. Missing corresponding regions reduce confidence, not a cue to
force the whole histogram or average color to match. Several references require roles.
Explain conflicts with protected skin/product colors before sacrificing those constraints.
Changing brief mode/references deactivates the old plan; historical plans remain readable.
Do not transfer its progress to a new reference analysis.

For feedback, keep the actual user message and add `interpretation`:
`{problem,region,direction,method,risks}`. `method` is global/color_range/local/inspect_only;
it describes an intended approach, never an automatic mask or parameter recipe. Respect
capabilities and allowed_local. Explain the correction in one sentence and proceed when
clear. Ask only if ambiguity would materially change the result. Read and render again
after manual edits; feedback tied to a stale checkpoint must not drive a new adjustment.

Example: "The face is good, make greens quieter, do not darken the photo" means:
problem=distracting greens, region=foliage, direction=more restrained green,
method=color_range if appropriate, risks=neighboring skin/clothing colors. Append
brightness to feedback `protect`, then use `recognize` to record face approval ONLY if
the user actually said it. Payload:
`{checkpoint_id,aspect:"current face color and modeling",user_confirmation:"the face is good"}`.
Recognition can reference an older reviewed checkpoint. It is a comparison anchor,
not whole-image acceptance, pixel locking, automatic mixing of versions, or a permanent preference.
Only use `supersedes:[recognition_id]` when the user explicitly replaces an earlier approval;
the original declaration remains in history as inactive. No preference library is created.

New checkpoints freeze the current reference plan, unresolved interpreted feedback, brief
protections and active recognitions under `intent`. To compare an older recognized version,
use `compare {checkpoint_ids:[...]}` (one to three, without roles). Check the returned
restore scope before promising that historical full-image appearance can be recreated.

For P1 checkpoints, submit a review result for each captured item as `{id,status,note}`:

| Field | IDs from checkpoint.intent | Allowed status |
|---|---|---|
| target_results | reference_targets | pending / close / conflict / limited |
| protection_results | protections | preserved / changed / uncertain |
| feedback_results | feedback | resolved / improved / unchanged / worse / conflict |

Use `intent.review_required` to identify mandatory P1 protection checks. Assess all captured
items exactly once, including limited traits, and explain what remains in each note.
See previous feedback's originating checkpoint and recognized anchors when judging changes;
do not mark preserved just because controls were unchanged. Returned hashes identify the
viewed image, not proof of a correct visual judgment. There is no similarity percentage.

Present the result as gains, preserved details, and remaining differences. Changed protections,
worse/conflicting feedback or conflicting traits put the task in needs_direction; uncertain
protection puts it in needs_verification. Follow that status: compare, inspect or ask for
direction before further aesthetic trials. Do not auto-rollback unrelated manual edits.
`accept` remains reserved for actual full-version user acceptance.
