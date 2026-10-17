- [ ] `telemetry-consent-dialog.tsx`: replace the four stacked paragraphs with
  one summary paragraph; move the raw envelope behind a `Collapsible`
  disclosure that is closed by default and sits below the summary and above the
  footer; keep both decision buttons on the same variant, side by side.
- [ ] `telemetry-consent-dialog.tsx`: persist dismissal in `localStorage` under
  a single key, following the `use-dashboard-preferences.ts` guarded-access
  pattern; a dismissal must not issue any consent mutation.
- [ ] i18n `en`/`ko`/`zh-CN`: fold `consentDialog.categories` into a shortened
  `consentDialog.description`; drop `optOutNotice` from the dialog (the key
  stays, the settings card still uses it); replace `consentDialog.payloadLabel`
  with a disclosure trigger string.
- [ ] `telemetry-consent-dialog.test.tsx`: assert the envelope is absent until
  the disclosure is opened and present after; assert both actions are present
  with equal prominence; assert Escape persists nothing and that a second mount
  does not present the dialog.
- [ ] `__integration__/telemetry-consent-flow.test.tsx`: keep the decision-flow
  assertions; clear the dismissal key between tests so the existing cases still
  see the dialog.
- [ ] Confirm no backend, schema, settings or payload change; `[settings_fields]`
  untouched.
- [ ] Capture before/after screenshots for the PR.
- [ ] `openspec validate --specs`, `bun run lint`, `bun run test`.
