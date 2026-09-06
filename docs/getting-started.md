# Getting started with Aura

Start with a project folder and either a hosted API key or a running local model server.

## Install

### Windows installer

Download the latest `.exe` from [GitHub Releases](https://github.com/CarpseDeam/Aura-IDE/releases/latest) and run it. Python is bundled. Installation is per user and does not need administrator rights. Launch Aura from the Start menu.

### From source

Use Python 3.10 or newer and Git. These commands work from a terminal on Windows, macOS, or Linux:

```bash
git clone https://github.com/CarpseDeam/Aura-IDE.git
cd Aura-IDE
python -m venv .venv
```

Activate the environment using the command for your shell:

| Shell | Command |
| --- | --- |
| Windows PowerShell | `.venv\Scripts\Activate.ps1` |
| Windows Command Prompt | `.venv\Scripts\activate.bat` |
| macOS / Linux | `source .venv/bin/activate` |

If your system uses `python3` instead of `python`, use it to create the environment. Then install and launch:

```bash
python -m pip install .
aura
```

For development, use `python -m pip install -e ".[dev]"` instead. `python -m aura` is another way to launch the app.

## Connect a model

Open Settings using the gear button. The onboarding wizard can also take you there. You can browse the app before configuring a model.

### Hosted models

Add your provider's key in **Settings → API Keys**, then select the provider and model in **Settings → Models**. Aura supports DeepSeek, OpenAI, Anthropic, Gemini, and OpenRouter.

Environment variables are also supported:

| Provider | Environment variable |
| --- | --- |
| DeepSeek | `DEEPSEEK_API_KEY` |
| OpenAI | `OPENAI_API_KEY` |
| Anthropic | `ANTHROPIC_API_KEY` |
| Gemini | `GEMINI_API_KEY` |
| OpenRouter | `OPENROUTER_API_KEY` |

Your provider bills API usage separately from Aura.

### Local models

Start your local model server first. In **Settings → Models**, select **Local Model**, enter its OpenAI-compatible base URL, and choose **Test / Discover**. Select a discovered model.

| Server | Common local base URL |
| --- | --- |
| Ollama | `http://127.0.0.1:11434/v1` |
| LM Studio | `http://127.0.0.1:1234/v1` |
| llama.cpp | `http://127.0.0.1:8080/v1` |

Local models do not require a hosted API key. Aura does not download models or start the server. Choose a model/server with tool-calling support and enough context for coding work.

[Full provider guide →](providers.md)

## Run your first task

1. Use **Change Folder…** to open a small project, or **New Project** to start one.
2. Select your model and thinking level in the sidebar.
3. To inspect the project first, enable **Read Only** and ask: **“Explain this project and show me how to run its tests.”**
4. For an edit, turn Read Only off. Keep **Auto-Approve** disabled if you want to review proposed writes. The toolbar's **Approve** switch controls *automatic* approval; leave it off for manual diff review.
5. Ask for a small change: **“Find a function that needs a test, add one, run it, and explain the result.”**
6. Review proposed diffs and follow the terminal output and validation results. Read Aura's final report for what changed, which checks ran, and anything unresolved.

The **Plan** switch lets you review a plan before changes. The **Workspace** button on the right rail hides or restores the files and code pane without losing its contents.

## Try Agents and Teams

Turn **Agents on** to let Aura assemble a team or use a runnable saved Team in ordinary chat. The conversation remains in the main window, with cards showing the delegated work.

To build a reusable process, ask:

> Create a reusable workflow that implements a requested change, tests it, then reviews it.

Aura saves the workflow and shows a graph card. Creating it does not run the task. Inspect **Details**, refine it in chat, or use **Open Workflow** to open the Team editor. Click **Run** and provide a task when ready.

Saved Teams appear in the **Agents & Teams** library. A card's Run uses that exact saved workflow even if a different Team is open in the editor. Both the saved workflow and its chat card remain available after restarting Aura.

[Create, edit, and reuse Teams →](agents-and-teams.md)

## Shortcuts

| Shortcut | Action |
| --- | --- |
| Ctrl+Enter | Send, or queue a message during a run |
| Ctrl+Shift+A | Ask about the selected code |
| Ctrl+V in the input | Paste a screenshot |

Type `/help` for available commands. `/undo` restores the last checkpoint or pre-run snapshot; review the current Git state before using it.

## If something gets stuck

- **No model available:** Check the hosted key or confirm the local server is running, then revisit Settings → Models.
- **Aura cannot edit:** Check Read Only, Agent permissions, and any pending plan or diff approval.
- **Local model errors:** Check the server's tool-calling support and context configuration.
- **Missing validation tools:** Install the tools required by the project you opened and make sure they are available to Aura.

For help, bring the steps you tried and the relevant error to [GitHub Issues](https://github.com/CarpseDeam/Aura-IDE/issues) or [Discord](https://discord.gg/aGSthBX2Bg). Remove API keys and private project content before sharing logs.
