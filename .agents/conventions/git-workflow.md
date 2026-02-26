# Git Workflow & Contribution

1. **Important**: Create branches, commits, or PRs **only upon explicit user request**. Implicit actions are not allowed.
2. **Branch Naming**: Use prefixes like `feature/`, `fix/`, `chore/` (e.g., `feature/add-login`).
3. **Commit Messages**: Follow [Conventional Commits](https://www.conventionalcommits.org/).
   - Format: `<type>(<scope>): <description>`
   - Types: `feat`, `fix`, `docs`, `refactor`, `chore`, `test`
   - Example: `feat(api): add auth endpoint`
4. **Workflow**:

   ```bash
   git checkout -b feature/add-login
   git commit -m "feat(api): add auth endpoint"
   # Only on explicit request:
   git push -u origin feature/add-login
   gh pr create --title "feat(api): add auth" --body "..."
   ```

5. **Best Practices**: Commit often in small units. Do not commit directly to `main`. Always check `git diff` before pushing.
