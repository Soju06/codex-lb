# Verified browser evidence

Captured in Chromium on 2026-09-10 using the built dashboard and an isolated real backend, with Reasoning enabled. Before: product dialog components from main `43a45f78`, rebuilt before capture. After: this PR's components, rebuilt after restoration. Notifications expired before capture; no live account credentials or external upstream traffic were used.

| Viewport / dialog | Before dialog y range | Before submit y range | After dialog y range | After submit y range |
|---|---|---|---|---|
| 320x568 create | -551..1119 | 1058..1094 | 16..552 | 499..535 |
| 320x568 edit | -561..1129 | 1068..1104 | 16..552 | 499..535 |
| 1440x900 create and edit | -118..1018 | 957..993 | 16..884 | 831..867 |

The `before-*` and `after-*` PNG files cover both dialogs at both sizes. The permanent browser suite additionally covers 390x844, internal scrolling, field spacing on edit, Escape dismissal, and shell overflow clipping. It passed all 7 tests after the final shell assertion change. Focused model-source tests passed 19 tests in 4 files; frontend typecheck, lint and build passed. The capture uses a programmatic toggle for the unreachable baseline Reasoning control; permanent regressions use normal user interactions.
