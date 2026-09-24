# Follow-up Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use inline execution with task-by-task checkpoints.

**Goal:** Complete the four explicitly unresolved verification items: glyph-aware motion matching, real color-mask validation, hand-edge refinement, and phone11 frame-identity review.

**Architecture:** Keep the existing static/trajectory split. D1 will add glyph matching as the preferred measurement in `verify_line_tracks`, with the current full-patch matcher as a recorded fallback. B3/B4 will use a copied ROI and isolated outputs, then render deterministic frame comparisons. phone11 will use existing source frames and ASS events to report row identity without changing text solely because it persists on screen.

**Tech Stack:** Python 3.12, OpenCV, NumPy, pytest, FFmpeg/libass, existing `.venv`.

**Spec:** `docs/FINAL_OPTIMIZATION_DESIGN.md` and the approved design in `docs/FINAL_IMPLEMENTATION_PLAN.md`.

## Global Constraints

- Preserve `baseline/real-video-optimization-20260923`; do not modify source videos or original ROI files.
- Use only existing dependencies and the project `.venv`.
- Unknown glyph/contour evidence must fall back conservatively; never invent motion, clear frames, or foreground masks.
- Real video evidence must be written to a new output directory and clearly separated from prior evidence.
- Existing static text and true moving text must both remain supported.

### Task 1: D1 glyph matcher production wiring

**Files:**
- Modify: `core/pose_verify.py` measurement path around `_match_center` and `verify_line_tracks`.
- Modify: `core/step_segmentation.py` only if the new report state needs propagation.
- Test: `tests/test_pose_verify.py`, `tests/test_text_motion_evidence.py`, `tests/test_motion_ass_cli.py`.

**Interfaces:** Use `glyph_mask_from_reference(reference_gray, box)` and `masked_text_match(frame_gray, reference_gray, glyph_mask, search_box)`. Preserve `LineVerifyReport.measured` and add a diagnostic field only if needed; old callers remain valid.

- [ ] Add a regression test where a fixed glyph sits on a horizontally moving textured background; assert the verified line is static and emits no `move` geometry.
- [ ] Add a regression test where the glyph itself moves; assert the verified line remains moving and produces trajectory geometry.
- [ ] In the reference-frame path, create a glyph mask from the OCR line box. For each sampled frame, call `masked_text_match` first; if it returns `None`, call the existing matcher and record the fallback reason.
- [ ] Treat missing/ambiguous glyph masks as `unknown`; do not convert unknown into static or moving evidence.
- [ ] Run the focused pose and motion tests, then the full suite.

### Task 2: B3 real color-mask validation

**Files:**
- Create: `test_run/followup_verification_20260924/` with `outputs`, `frames`, and `logs`.
- Copy and modify only the ROI configuration used for the mail sample; keep the original ROI untouched.
- Test: existing `tests/test_scene_brightness.py`, `tests/test_scene_policy_slices.py`, plus a standalone render check.

**Interfaces:** Use existing `scene_text_policy=mask` and `roi_auto_brightness`; inspect generated `\\p1` events for `\\1c` and absence of `\\alpha`.

- [ ] Generate an isolated mail mask ASS from a copied ROI.
- [ ] Render bright and dark source frames with the generated ASS.
- [ ] Assert mask color changes with background luminance, remains opaque, and does not reveal the original text through alpha.
- [ ] Save the ASS, command, logs, and comparison frames; record whether the actual policy stayed `mask` or fell back.

### Task 3: B4 hand-edge refinement and render review

**Files:**
- Modify only if evidence shows a defect: `core/occluder_contours.py`, `core/subtitle_generator/generator.py`, related tests.
- Test: `tests/test_occluder_contours.py`, `tests/test_occlusion_render.py`.

**Interfaces:** Keep `ContourResult(valid/clear/unknown)` and `apply_screen_occlusion` contracts; screen coordinates are mapped to ASS exactly once.

- [ ] Render f216, f222, and f228 from the protected mail output beside raw and ASS-only frames.
- [ ] Measure residual subtitle ink inside the hand target and untouched-region differences.
- [ ] If residuals are caused by edge under-segmentation, add a deterministic edge refinement using existing contour seeds while preserving holes; otherwise record the evidence as a remaining limitation.
- [ ] Re-run synthetic contour tests and the isolated mail render.

### Task 4: phone11 row-identity review

**Files:**
- Create: `test_run/followup_verification_20260924/phone11_row_review.json` and frame annotations.
- Modify code only if the review identifies an actual wrong row identity or time interval.
- Test: relevant phone11/scene timeline tests.

**Interfaces:** Compare source-frame visible row boxes with ASS event `\\pos`/text and `[start,end)` intervals; persistent visible rows are valid evidence, not leakage by themselves.

- [ ] Inspect f24, f240, and f527 plus neighboring frames.
- [ ] For each event, classify `visible`, `not-yet-visible`, `already-left`, or `ambiguous`.
- [ ] Report the first frame of any genuine mismatch and trace it to OCR grouping, fidelity evidence, or event timing before changing code.
- [ ] Re-run the phone11-specific tests and update the final plan with evidence and any residual ambiguity.

### Task 5: Final validation and delivery record

- [ ] Run `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q`.
- [ ] Run `.venv/bin/python -m ruff check --select F . --exclude docs` and `git diff --check`.
- [ ] Update `docs/FINAL_IMPLEMENTATION_PLAN.md` with exact outputs, limitations, and commit hashes.
- [ ] Commit each code change with a focused message; keep evidence files separate from source changes.
