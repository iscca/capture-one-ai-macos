---
name: capture-one-edit
description: Read and grade photos in the user's open Capture One Session on macOS through the local Capture One AI MCP or CLI. Use for reference color matching, film and cinematic looks, and native adjustment or mask work; requires the installed local bridge and a vision-capable client.
---

# Capture One editing on Mac

Edit the photo selected in Capture One, preserving the source and judging changes from actual Capture One renders. This skill supplies editing judgment and workflow; the separately installed local bridge executes requests. It does not install Capture One, grant permissions, or connect a remote photo service.

## Connect and inspect

Use the connected Capture One AI MCP tools when available: `context`, `images`, `read`, `adjust`, `preview`, `operations`. Otherwise use the installed `c1ai` CLI or the configured bridge Python environment; read [references/usage.md](references/usage.md) for examples. If the bridge is absent, explain the installation requirement using that reference. Do not guess machine paths or install from an unrelated repository.

Start with `context`, inspect platform capabilities, selected document/photo/layer, and pending operations. Read the target and view a current render before grading. Capabilities and the installed host version take precedence over this skill's examples.

- macOS: `preview(kind=render)` requires the user's explicit permission for temporary low-resolution JPEG export and corresponding local configuration. If already authorized for this session, continue without asking again. No verified native/no-export or grayscale-mask preview exists on Mac.

Stay within the user's selected Session and photo. Do not switch documents or start batch work without that scope. When the request is a new alternative, create one candidate with `images(action=create_candidate)`, verify the new target, and reuse it throughout the grade. For a continuing edit, reuse the intended registered candidate. Do not modify the original, borrow private candidate registries, or edit databases/sidecars.

## Grade from the image

Describe the intended tonal/color result briefly, unless already agreed. Select modules because they solve visible problems; do not treat API maxima or a fixed recipe as recommendations. Save initial module observations privately for deliberate restoration.

Use `read → adjust → readback → actual preview`. Patches contain absolute target values. Pass the fresh `target` and `observation_token` unchanged; every new mutation gets a unique `request_id`. Related writes to one module can share a visual review. After changing photos or layers, read again.

Typical choices are white balance, exposure/HDR, levels/curves, mixer, color balance, then grain. Skip unnecessary modules. Read [references/usage.md](references/usage.md) for channel conventions and platform gaps.

For film/cinematic requests, reason separately about highlight rolloff, shadow density, midtone contrast, black floor, color relationships, and grain. Keep light sources plausible and protect important detail. Use channel curves, mixers and wheels for an interpreted film palette; do not call it a measured film emulation or an official LUT without evidence. Grain should complement image scale and texture; resized previews do not prove full-resolution grain quality.

Add local work only for a visible remaining problem. Inspect actual rendered boundaries and subject/background effects. On Mac, a completed mask command is not pixel verification; AI-layer fill may retain generated/submask constraints. Do not use unavailable layer sorting, duplication, gradients, or mask combinations.

## Stop and deliver

For `outcome_unknown`, timeout or `partial`, query the original `request_id` with `operations`; reconcile using the existing receipt when required. Do not replace its ID and resend. A stale observation requires a fresh read, not removal of target/old-value guards. Do not promise transactional rollback of multi-parameter changes.

Do not fall back to mouse/keyboard, screenshot color sampling or preset import without explicit user choice. Respect a user's decision to pause interface assistance. Sharpening, denoising, lens/geometry changes, retouching, final exports and uploading photos are outside an ordinary color-grade request.

Stop once the agreed look is achieved. Leave the candidate selected, summarize the visible result and material limitations, and show the actual final preview when available. Never claim to have seen an image from numeric parameters or an embedded camera JPEG. Keep previews, Session names/paths, photo identifiers, receipts and tokens local; sharing this skill does not authorize sharing photo material.
