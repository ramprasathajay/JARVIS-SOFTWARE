# JARVIS-SOFTWARE

A Python-based personal AI assistant inspired by JARVIS. It supports text and voice interaction, Groq-powered chat, safe desktop automation, browser actions, and workspace file operations.

## Overview

JARVIS-SOFTWARE is designed to act as a local AI helper for everyday tasks such as:

- Chatting with a Groq-backed language model
- Opening or closing approved desktop applications
- Searching and opening websites in the browser
- Creating, reading, renaming, and deleting files within a safe workspace
- Writing code into supported project files
- Typing and reading local assistant messages
- Displaying system and process information
- Running only approved, user-confirmed commands

## Features

### AI chat
- Text-based conversation mode
- Voice interaction mode using speech recognition and text-to-speech
- Uses the Groq API when `GROQ_API_KEY` is configured

### Desktop and browser control
- Open and close approved applications
- Launch browser search or URLs with user approval flow
- Open project folders

### Workspace tools
- Create folders and files
- Read and rename files safely inside the workspace
- Delete files only after confirmation
- Write Python, JavaScript, and other supported source code files

### Safety controls
- Workspace paths are restricted to the configured project folder
- Commands are limited to a safe allowlist
- Dangerous shell operators and risky actions are blocked
- Critical operations require confirmation before execution

## Run the assistant

```bash
python main.py
```

Voice mode:

```bash
python main.py --mode voice
```

## Environment variables

Set these variables before running the app if needed:

```bash
export GROQ_API_KEY="your_api_key"
export GROQ_MODEL="groq/compound"
export JARVIS_WORKSPACE_ROOT="/path/to/project"
export JARVIS_ENABLE_COMMANDS="1"
```

## Notes

This project is intended as a local assistant and includes safety checks around command execution, browser access, and file operations. Some advanced features require the relevant Python packages and optional environment configuration.

## License

This project is licensed under the MIT License. See the `LICENSE` file for details.
