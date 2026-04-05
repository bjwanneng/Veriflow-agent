# 🤖 CLAUDE.md - Development Protocol

## 🛠 Commands
- Install: `npm install`
- Build: `npm run build`
- Test: `npm test`
- Lint/Fix: `npm run lint --fix`

## ⚖️ Core Instructions (CRITICAL)
1. **Always Sync First**: Before any action, you MUST read `readme_first.md` to align with the current progress and context.
2. **Task Decomposition**: Before coding, output a `/plan`. Break large tasks into small, atomic sub-tasks.
3. **Incremental Execution**: Implement one sub-task at a time. Run tests immediately after. Do not proceed until verified.
4. **Mandatory Documentation**: Update `readme_first.md` after every task completion or before ending the session.
5. **Cleanup Duty**: Before declaring a task "Done," delete any temporary files or move them to the scratch directory.

## 📁 Filesystem Hygiene & Organization
1. **No Root Clutter**: Never create temporary scripts or debug files in the root directory.
2. **Scratch Folder**: All temporary logic spikes, debug scripts, or experimental code MUST be placed in `.claude/scratch/`.
3. **Test Locations**: Tests must reside in `tests/` or `__tests__` folders. Use `.test.ts` or `.spec.ts` suffixes.
4. **Naming Convention**: Use **kebab-case** for all new files (e.g., `auth-provider.ts`).
5. **Git Safety**: Ensure `.claude/` is ignored by Git. Do not commit scratch files.

## 🔄 Session Handoff & Context Management
1. **Context Refresh**: If the conversation becomes too long or logic becomes slow, you MUST suggest a session restart.
2. **Pre-Restart Summary**: Before a restart or ending a session, update `readme_first.md` with a "Session Handoff" section.
3. **Handoff Content**: Include:
   - What was just accomplished.
   - The exact file and line number where we stopped.
   - Any variables or specific logic that was in the "active memory" but not yet committed to code.

## 🎨 Coding Standards
- Style: Use TypeScript with functional patterns. Prefer clarity over cleverness.
- Error Handling: Use descriptive error messages. No silent failures.
- Testing: Every feature requires a corresponding test.