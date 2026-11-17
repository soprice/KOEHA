# FloaSub floating subtitles

FloaSub is KOEHA's floating-caption presentation and playback interface. Configure it from **Overlay** in the sidebar. The embedded preview and floating overlay share the same caption canvas and appearance settings.

## Subtitle flow

The **Playback** category provides three flows:

| Flow | Behavior |
| --- | --- |
| **Smart Dual · Committed + Live** | Keeps finalized subtitles in the main panel while presenting ongoing recognition in a separate live strip. |
| **Committed Only · In order** | Presents finalized lines in order, waiting for translations when configured. Live drafts are not displayed. |
| **Live Single · Instant slot** | Prioritizes the newest recognized text in one caption slot, with a minimum reading interval for finalized translations. |

Smart Dual's **Live preview strip** has three modes: **Full** (original and draft translation), **Raw** (original only), and **Off** (committed captions only). **Translate live previews** separately controls whether draft translation requests are enabled. When it is disabled, Full mode does not create preview translations.

Draft translations are provisional. A final translation supersedes its draft; a draft must not overwrite a finalized caption. Playback uses caption IDs to preserve order and bounds the number of pending captions. Translation failures preserve the original subtitle.

## Timing and interaction

The Playback inspector provides **Wait for translation**, **Minimum reading time**, **Sync delay**, and **Auto-hide on silence**. These affect subtitle presentation, not recognition model decoding. Auto-hide set to **Never** disables silence-based hiding. The window also supports automatic opening when Live starts and an optional click-through lock.

The floating caption window can size its pill to the active text when **Fit width to text** is enabled, subject to the configured maximum width. A hover toolbar provides overlay controls. With click-through lock enabled, mouse input passes to windows underneath rather than being captured by the overlay.

## Appearance

The **Layout** category contains style presets, width, original-line visibility, text shadow, background, and border controls. The **Typography** category contains font, text colors, and outline settings. The preview updates as appearance settings change; changes are saved automatically.

The rendered caption layout may include a finalized source line, a finalized translated line or pending-translation indication, a live source draft, and an optional live translation draft. Pending and provisional states are visually distinguished.

## Settings compatibility

The settings loader maps legacy overlay timing values `immediate` and `delayed` to the corresponding new flows and supplies defaults for newly added fields. Unknown profile fields are ignored during profile loading for compatibility across versions.

## Implementation reference

- `src/koeha/overlay/playback.py`: subtitle state, queues, translation readiness, timers, committed/live coordination
- `src/koeha/overlay/captions.py`: shared caption rendering, fit-width geometry, floating window and toolbar
- `src/koeha/overlay/studio.py`: playback, layout and typography controls
- `src/koeha/core/settings.py`: persistent settings and migration
- `tests/test_overlay_playback.py`, `tests/test_caption_style.py`: related regression coverage

## Validation

The branch adds automated test coverage for playback, caption style, UI, translation, and connection behavior. These changes are not proof of a passing CI run or physical Windows audio validation. Run `./scripts/check.ps1` and review GitHub Actions checks on the combined PR before release.
