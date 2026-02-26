# Git Workflow & Contribution

1. **Important**: Create branches, commits, or PRs **only upon explicit user request**. Implicit actions are not allowed.
2. **Branch Naming**: Use prefixes like `feature/`, `fix/`, `chore/` (e.g., `feature/add-login`).
3. **Commit Messages**: Follow [Conventional Commits](https://www.conventionalcommits.org/).
   - Format: `<type>(<scope>): <description>`
   - Types: `feat`, `fix`, `docs`, `refactor`, `chore`, `test`
   - Example: `feat(api): add auth endpoint`
4. **PR Titles**: Follow [Conventional Commits](https://www.conventionalcommits.org/) — same format as commit messages.
   - Format: `<type>(<scope>): <description>`
   - Types: `feat`, `fix`, `docs`, `refactor`, `chore`, `test`, `ci`, `perf`, `build`
   - Breaking changes: append `!` before colon — `feat(api)!: remove v1 endpoints`
   - Example: `fix(auth): handle expired refresh token`
5. **Workflow**:

   ```bash
   git checkout -b feature/add-login
   git commit -m "feat(api): add auth endpoint"
   # Only on explicit request:
   git push -u origin feature/add-login
   gh pr create --title "feat(api): add auth endpoint" --body "..."
   ```

6. **Pushing to Fork PRs**: When a PR comes from a fork (cross-repository), push
   commits directly to the fork's head branch instead of creating a separate PR.

   ```bash
   # 1. Check PR head info
   gh pr view <N> --json headRefName,headRepositoryOwner,isCrossRepository

   # 2. Add fork remote (if not already added)
   git remote add <fork-owner> https://github.com/<fork-owner>/<repo>.git
   git fetch <fork-owner> <head-branch>

   # 3. Checkout fork branch, apply changes, push
   git checkout -b <fork-owner>-<head-branch> <fork-owner>/<head-branch>
   # ... make changes and commit ...
   git push <fork-owner> HEAD:<head-branch>
   ```

   Note: This requires "Allow edits from maintainers" to be enabled on the PR.

7. **Best Practices**: Commit often in small units. Do not commit directly to `main`. Always check `git diff` before pushing.
