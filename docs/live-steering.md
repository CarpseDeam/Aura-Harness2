# Live task updates

While a task is active, the composer offers **Send update** (also Ctrl+Enter)
and **Queue next task**. Idle sending is unchanged. Updates currently accept
text; attachments and selected skills belong to a queued/new request. An
update keeps the active task's model, backend, tools, permissions, approval
policy and frozen workflow. Stop still cancels the task.

The update card distinguishes pending delivery, addition to model context, and
an update that could not be delivered before the task ended. Addition to context
is evidence of delivery to at least one participating loop, not evidence that
the model followed the correction. A late submission is shown for copying and
returned to an empty composer; it never replaces a newer draft.

## Owners and boundaries

- `InputPanel` captures routing intent. `SendHandler` owns FIFO snapshots and
  routes corrections without starting a new send or changing task authority.
- `ConversationBridge` opens one `TurnUpdates` log before starting the worker.
  Submission and root completion share the log's lock, so an accepted update
  either causes another model request or remains visibly undelivered on failure.
- `ConversationManager` owns canonical history and the task's frozen state.
  `AgentLoop` reads through its own cursor only between complete message blocks.
  It checks again after provider responses, including responses without tools.
- `ToolRoundRunner` admits each tool against that cursor inside the worker.
  Already-started calls settle normally. Calls still waiting in an executor,
  and later serial calls, receive explicit unsuccessful `not_executed` results
  when the user changes the task. Results are appended once, in call order,
  before any user correction is added to the next request.
- Individual delegation, automatic Teams, saved Workflows and nested helpers
  pass the same task log through their existing execution owners. Every child
  creates a fresh cursor; active children reconsider at their own boundaries,
  and later children receive preceding corrections. Worktree and workflow
  scheduling ownership are unchanged.
- Canonical history and chat persistence retain corrections as genuine user
  updates. Retry retains the original request, its corrections and the real
  effects/results preceding the latest correction. Local receipt metadata is
  stripped from provider payloads. Retried children inherit the corrections.

Failure and Stop close submission and retain undelivered corrections for retry
or copying. Reset and workspace changes invalidate the old delivery scope.
Steering does not undo completed work or cancel an already-started operation.

## Verification

`tests/test_turn_updates*.py` covers streaming, serial and parallel tools,
executor queues, completion races, Stop/failure, child delivery, FIFO snapshots,
frozen state, and persisted/retried corrections. Existing conversation, Agent,
workflow, approval and GUI regressions exercise the same production paths.

A separate visible desktop smoke check uses the real `MainWindow`, composer,
bridge and tool runtime with a scripted provider and Qt mouse/key input. It was
run against `C:\Projects\workflow_agent` with Agents ON and Approve OFF: a real
README read settled, the proposed write was skipped, the queued task drained,
and Stop preserved the newer draft. Screenshots were visually inspected. This
checks desktop behavior, not compliance by a live hosted model; the native
desktop automation helper was unavailable in this environment.

The broad process-isolated run reported 2,450 passing tests and three skips.
`test_aura_glow_halo.py` passed its 18 tests but the process then exited with a
native access violation; the same module also exited unsuccessfully after all
18 tests passed on the untouched `e0daf2f` base checkout. It is not a clean
full-suite exit. The final focused runs passed 177 runtime and 29 composer/GUI
tests; lint, compilation and `git diff --check` passed.
