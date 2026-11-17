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

Draft translations are provisional. Each carries the exact source text it translated so the live strip can indicate when the recognition preview has moved on. A final translation supersedes its draft. Final translation requests run in order on a separate worker, and each request can reference up to four earlier finalized source lines. Playback uses caption IDs to apply updates and keeps a bounded queue of final captions. If a translation fails or the wait limit expires, the original subtitle can still be displayed.

## Timing and interaction

The Playback inspector provides **Wait for translation**, **Minimum reading time**, **Sync delay**, and **Auto-hide on silence**. These affect subtitle presentation, not recognition model decoding. Auto-hide set to **Never** disables silence-based hiding. The window also supports automatic opening when Live starts and an optional click-through lock.

The floating caption window can size its pill to the active text when **Fit width to text** is enabled, subject to the configured maximum width. A hover toolbar provides overlay controls. With click-through lock enabled, mouse input passes to windows underneath rather than being captured by the overlay.

## Appearance

The **Layout** category contains style presets, width, original-line visibility, text shadow, background, and border controls. The **Typography** category contains font, text colors, and outline settings. The preview updates as appearance settings change; changes are saved automatically.

The rendered caption layout may include a finalized source line, a finalized translated line or pending-translation indication, a live source draft, and an optional live translation draft. Pending and provisional states are visually distinguished.

## Settings compatibility

The settings loader maps legacy `immediate` timing to Smart Dual and `delayed` timing to Committed Only. It keeps the configured delay for delayed mode and supplies defaults for newly added fields. Saved application-audio selections, Temperature and Top P load alongside FloaSub settings. Unknown fields are ignored during profile loading for compatibility across versions.

## Implementation reference

- `src/koeha/overlay/playback.py`: subtitle state, queues, translation readiness, timers, committed/live coordination
- `src/koeha/overlay/captions.py`: shared caption rendering, fit-width geometry, floating window and toolbar
- `src/koeha/overlay/studio.py`: playback, layout and typography controls
- `src/koeha/core/settings.py`: persistent settings and migration
- `tests/test_overlay_playback.py`, `tests/test_caption_style.py`: related regression coverage

## Validation

Playback, rendering, preview controls and translation ordering have automated regression coverage. See [Validation](VALIDATION.md) for Windows checks and their coverage.
