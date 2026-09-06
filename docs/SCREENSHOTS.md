# Keeping Aura's screenshots current

The README and GitHub Pages use real Aura sessions. Refresh these pictures when the relevant UI changes. A coding agent with desktop access can repeat the capture; no recording is required.

## Current assets

The images in `docs/assets/` are byte-for-byte copies of the source PNGs. Page layouts sometimes show a cropped view; every app screenshot links to its full original.

| View | Source in `media/` | GitHub Pages copy |
| --- | --- | --- |
| Workspace with code and completed work | `workspace-open-1350x800.png` | `docs/assets/workspace.png` |
| Workflow created in conversation | `workflow-conversation-created.png` | `docs/assets/team-created.png` |
| Team editor | `team-editor-1350x800.png` | `docs/assets/team-editor.png` |
| Completed coding task | `workflow-conversation-result.png` | `docs/assets/team-result.png` |
| Teams library | `teams-library-1350x800.png` | `docs/assets/teams-library.png` |
| Diff approval dialog | `diff-view.png` | `docs/assets/diff-view.png` |

The conversation captures came from the live walkthrough committed in `e5ec0b3`. The workspace, library, and editor captures came from the polish pass in `ec3de1c`. The diff dialog is an earlier reference capture of the approval UI.

The walkthrough used a separate `workflow_agent` project with Agents on, DeepSeek V4 Flash, and Thinking off. One saved workflow completed a greeting-helper task and then a CLI task; the latter finished with 10 passing tests. Those results describe that demonstration, not a benchmark or a guarantee for other projects.

## Capture checklist for a coding agent

1. Launch the current Aura source or packaged release and record its version/commit. Use a separate demo project and the configured model. Keep **Agents on** for the main walkthrough.
2. Create a reusable workflow through chat: implement a requested change, test it, then review it. Capture the request and the actual saved graph card before execution.
3. Open the Team editor. Arrange and fit the graph at a normal desktop window size, around 1350 × 800, with readable nodes. Capture the complete process.
4. Show the Team in the library with a useful name and description. Capture the library.
5. Run a small real coding task through the card. Verify the resulting files and tests, then capture the actual result. Open a changed file beside the conversation for the workspace image.
6. With Auto-Approve disabled, capture a real proposed change in the diff review dialog.
7. Inspect every picture for legibility, unexpected dialogs, personal information, and credentials. Keep real model output and visible results intact.
8. Update the source screenshots and their matching Pages copies together. Update image dimensions, alt text, captions, and any stated test results when the example changes.

## Preview the public pages

From the repository root:

```bash
python -m http.server 8000 --directory docs
```

Open `http://localhost:8000/`. Check desktop and phone widths, the three tour steps, keyboard navigation, Copy prompt, and the links to full screenshots. The full tour and download links must also work with JavaScript disabled.

GitHub Pages is served beneath `/Aura-IDE/`; keep local asset paths relative so they work under that prefix.

## Social preview

The Pages Open Graph and Twitter metadata use `docs/assets/workspace.png`, so sharing the page shows the actual app. Update the Open Graph dimensions if the workspace capture changes size.

GitHub's repository social-preview setting is separate from the Pages metadata.
