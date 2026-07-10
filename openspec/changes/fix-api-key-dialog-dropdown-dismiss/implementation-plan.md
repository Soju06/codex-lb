# API Key Dialog Dropdown Dismissal Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep API-key create/edit dialogs open during interactions with their portalled dropdowns while preserving ordinary outside-click dismissal, then deploy and verify the fix on the Ubuntu host.

**Architecture:** A small API-key-specific event guard recognizes Radix dropdown content by its stable `data-slot="dropdown-menu-content"` marker. Both API-key dialogs pass the guard to `DialogContent.onInteractOutside`; integration coverage exercises the real edit flow rather than only testing the helper.

**Tech Stack:** React 19, TypeScript 6, Radix UI, Testing Library, Vitest, Bun, Docker, FastAPI static frontend packaging.

## Global Constraints

- Preserve normal click-outside dismissal outside owned dropdown content.
- Do not change API-key API, schema, authentication, routing, or enforcement semantics.
- Preserve the live `codex-lb-data` named volume and the existing container runtime configuration.
- Base the live image on deployed revision `17c1762d254b2f328b882d417adeb8ded2726ec4` (`v1.20.1`).
- Keep the patch limited to API-key dialogs, their regression tests, and required OpenSpec artifacts.

---

### Task 1: Add a product-path regression test

**Files:**
- Modify: `frontend/src/features/api-keys/components/api-key-edit-dialog.test.tsx`

**Interfaces:**
- Consumes: `ApiKeyEditDialog` and existing `/api/models` MSW fixture.
- Produces: regression test `keeps the dialog open while selecting a model and submits the selection`.

- [ ] **Step 1: Add the failing integration test**

Add a test that renders an open edit dialog with `allowedModels: ["gpt-5.5"]`, opens the `1 model selected` control, selects `gpt-5.6-sol`, asserts `Edit API key` remains present, clicks Save, and asserts `onSubmit.mock.calls[0][0].allowedModels` equals `["gpt-5.5", "gpt-5.6-sol"]`.

```tsx
it("keeps the dialog open while selecting a model and submits the selection", async () => {
  const user = userEvent.setup();
  const onSubmit = vi.fn().mockResolvedValue(undefined);
  const onOpenChange = vi.fn();

  renderWithProviders(
    <ApiKeyEditDialog
      open
      busy={false}
      apiKey={createApiKey({ allowedModels: ["gpt-5.5"] })}
      onOpenChange={onOpenChange}
      onSubmit={onSubmit}
    />,
  );

  await user.click(await screen.findByRole("button", { name: "1 model selected" }));
  await user.click(screen.getByRole("menuitemcheckbox", { name: "gpt-5.6-sol" }));

  expect(screen.getByRole("dialog", { name: "Edit API key" })).toBeInTheDocument();
  expect(onOpenChange).not.toHaveBeenCalledWith(false);

  await user.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
  expect(onSubmit.mock.calls[0][0].allowedModels).toEqual(["gpt-5.5", "gpt-5.6-sol"]);
});
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
cd frontend
bun test src/features/api-keys/components/api-key-edit-dialog.test.tsx -t "keeps the dialog open while selecting a model"
```

Expected: FAIL because the parent dialog closes or `onOpenChange(false)` is called after the portalled menu interaction.

- [ ] **Step 3: Commit the red regression test**

```bash
git add frontend/src/features/api-keys/components/api-key-edit-dialog.test.tsx
git commit -m "test(ui): reproduce API key dropdown dismissal"
```

### Task 2: Implement the targeted portal guard

**Files:**
- Create: `frontend/src/features/api-keys/components/api-key-dialog-interactions.ts`
- Create: `frontend/src/features/api-keys/components/api-key-dialog-interactions.test.ts`
- Modify: `frontend/src/features/api-keys/components/api-key-create-dialog.tsx`
- Modify: `frontend/src/features/api-keys/components/api-key-edit-dialog.tsx`

**Interfaces:**
- Produces: `preventApiKeyDialogDropdownDismiss(event: { target: EventTarget | null; preventDefault(): void }): void`.
- Consumes: Radix dropdown content marker `[data-slot="dropdown-menu-content"]`.

- [ ] **Step 1: Add focused helper tests**

Test that the helper calls `preventDefault` when the target is inside dropdown content and does not call it for an ordinary outside element.

```ts
import { describe, expect, it, vi } from "vitest";
import { preventApiKeyDialogDropdownDismiss } from "./api-key-dialog-interactions";

describe("preventApiKeyDialogDropdownDismiss", () => {
  it("prevents dismissal for portalled dropdown content", () => {
    const content = document.createElement("div");
    content.dataset.slot = "dropdown-menu-content";
    const item = document.createElement("div");
    content.append(item);
    const preventDefault = vi.fn();

    preventApiKeyDialogDropdownDismiss({ target: item, preventDefault });

    expect(preventDefault).toHaveBeenCalledOnce();
  });

  it("allows ordinary outside interactions", () => {
    const preventDefault = vi.fn();

    preventApiKeyDialogDropdownDismiss({
      target: document.createElement("div"),
      preventDefault,
    });

    expect(preventDefault).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run the helper test and verify RED**

Run:

```bash
cd frontend
bun test src/features/api-keys/components/api-key-dialog-interactions.test.ts
```

Expected: FAIL because `api-key-dialog-interactions.ts` does not exist.

- [ ] **Step 3: Add the minimal helper**

```ts
type OutsideInteractionEvent = {
  target: EventTarget | null;
  preventDefault(): void;
};

export function preventApiKeyDialogDropdownDismiss(event: OutsideInteractionEvent): void {
  if (
    event.target instanceof Element &&
    event.target.closest('[data-slot="dropdown-menu-content"]')
  ) {
    event.preventDefault();
  }
}
```

- [ ] **Step 4: Wire both API-key dialogs**

Import the helper into `api-key-create-dialog.tsx` and `api-key-edit-dialog.tsx`, then pass it to each dialog content:

```tsx
<DialogContent
  className="sm:max-w-3xl"
  onInteractOutside={preventApiKeyDialogDropdownDismiss}
>
```

- [ ] **Step 5: Run focused tests and verify GREEN**

Run:

```bash
cd frontend
bun test \
  src/features/api-keys/components/api-key-dialog-interactions.test.ts \
  src/features/api-keys/components/api-key-edit-dialog.test.tsx \
  src/features/api-keys/components/api-key-create-dialog.test.tsx
```

Expected: all selected test files PASS, including the product-path regression.

- [ ] **Step 6: Commit the implementation**

```bash
git add frontend/src/features/api-keys/components/api-key-dialog-interactions.ts \
  frontend/src/features/api-keys/components/api-key-dialog-interactions.test.ts \
  frontend/src/features/api-keys/components/api-key-create-dialog.tsx \
  frontend/src/features/api-keys/components/api-key-edit-dialog.tsx
git commit -m "fix(ui): keep API key dialogs open for dropdown interactions"
```

### Task 3: Run repository verification

**Files:**
- Modify: `openspec/changes/fix-api-key-dialog-dropdown-dismiss/tasks.md`

**Interfaces:**
- Consumes: completed frontend patch and OpenSpec change.
- Produces: verified build artifacts suitable for image packaging.

- [ ] **Step 1: Run the frontend quality gate**

```bash
cd frontend
bun test src/features/api-keys/components
bun run typecheck
bun run lint
bun run build
```

Expected: all commands exit 0 with no failing tests, type errors, lint errors, or build errors.

- [ ] **Step 2: Validate OpenSpec**

From the repository root:

```bash
openspec validate --specs
```

Expected: validation exits 0. If the CLI is unavailable, install/use the repository-documented OpenSpec runner and record that exact command in verification evidence.

- [ ] **Step 3: Mark completed OpenSpec tasks and commit**

Update only completed checkboxes in `tasks.md`, then:

```bash
git add openspec/changes/fix-api-key-dialog-dropdown-dismiss
git commit -m "docs(openspec): record dropdown fix verification"
```

### Task 4: Build and deploy the patched 1.20.1 image

**Files:**
- No repository file changes.
- Runtime target: `jianan@192.168.50.10`, container `codex-lb`.

**Interfaces:**
- Consumes: verified repository checkout and current container inspection.
- Produces: local image `codex-lb:gpt56-dropdown-fix` and a healthy replacement container using `codex-lb-data`.

- [ ] **Step 1: Capture rollback metadata**

```bash
ssh jianan@192.168.50.10 'docker inspect codex-lb' > work/codex-lb-container-before.json
ssh jianan@192.168.50.10 'docker image inspect ghcr.io/soju06/codex-lb:latest --format "{{.Id}}"'
```

Expected: inspection JSON is non-empty and the prior image ID is recorded.

- [ ] **Step 2: Build the patched image for the Ubuntu host architecture**

Use the repository Dockerfile and the host's reported architecture. Build/tag `codex-lb:gpt56-dropdown-fix`; transfer it with `docker save | ssh ... docker load` if building on macOS, or build from the verified source on the Ubuntu Docker host.

Expected: `docker image inspect codex-lb:gpt56-dropdown-fix` succeeds on Ubuntu.

- [ ] **Step 3: Replace the container without changing runtime configuration**

Recreate `codex-lb` from the captured inspection, preserving published ports `1455` and `2455`, restart policy, environment, command, and `codex-lb-data:/var/lib/codex-lb`. Do not delete or recreate the named volume.

Expected: the new container reports image `codex-lb:gpt56-dropdown-fix` and the same volume/ports as the inspection snapshot.

- [ ] **Step 4: Verify service and data**

```bash
curl -fsS http://192.168.50.10:2455/health
ssh jianan@192.168.50.10 'docker inspect codex-lb --format "{{.Config.Image}} {{json .Mounts}} {{json .HostConfig.PortBindings}}"'
```

Expected: health returns `{"status":"ok"}`, the named volume remains `codex-lb-data`, and both port bindings remain present.

### Task 5: Verify the original browser symptom and publish the upstream patch

**Files:**
- No additional code files unless verification exposes a regression.

**Interfaces:**
- Consumes: healthy live patched container and committed branch.
- Produces: browser evidence and an upstream pull request.

- [ ] **Step 1: Re-run the live browser reproduction**

Open `/settings`, edit `ubuntu-hermes-agent-gpt55-active`, open Allowed models, toggle one model off and back on, and verify the dialog remains visible after each click. Save and verify the table still lists `gpt-5.5`, `gpt-5.6-sol`, `gpt-5.6-terra`, and `gpt-5.6-luna`.

Expected: dialog stays open, Save completes, and the four-model allowlist persists after reload.

- [ ] **Step 2: Verify normal outside dismissal**

Reopen the edit dialog, click the overlay outside both the dialog and dropdown content, and verify the dialog closes.

Expected: ordinary click-outside dismissal remains functional.

- [ ] **Step 3: Push the branch and open the PR**

Push `fix/api-key-dialog-dropdown-dismiss` to the authenticated contributor fork/remote and create a draft PR against `Soju06/codex-lb:main`. The PR title is:

```text
fix(ui): keep API key dialogs open for dropdown interactions
```

The body must include the exact 1.20.1 reproduction, regression-test evidence, frontend gate results, OpenSpec validation, live deployment verification, and a linked issue using `Fixes #N` if an issue exists; otherwise state that no issue was filed and keep the PR draft until maintainer guidance.

- [ ] **Step 4: Report handoff and rollback**

Provide the PR URL, live image tag/ID, prior image ID, health result, GUI verification result, and the exact rollback command derived from `work/codex-lb-container-before.json`.
