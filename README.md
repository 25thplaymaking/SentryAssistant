# Sentry Assistant

Sentry Assistant is Bryce's local Windows personal assistant and code watcher.

## First milestone

- Native WinUI 3 dashboard with Assistant, Code watcher, Activity, and Settings pages.
- OpenAI Responses-based conversation with recent-message context.
- Push-to-talk voice replies that are transcribed into the message box for review before sending.
- One-sentence Jarvis-style spoken summaries with selectable OpenAI voices.
- Recursive folder watching with noise filtering and duplicate-event suppression.
- API key imported from the earlier Sentry Voice tool and protected with Windows DPAPI for the current user.
- Light, dark, high-contrast, keyboard, mouse, and touch behavior inherited from native WinUI controls.

## Local development

Use the 64-bit .NET SDK on this machine:

```powershell
& 'C:\Program Files\dotnet\dotnet.exe' build .\SentryAssistant.csproj -c Debug -p:Platform=x64
& 'C:\Program Files\dotnet\dotnet.exe' run --project .\SentryAssistant.csproj -c Debug -p:Platform=x64
```

The app is packaged and launched through the Windows App Development CLI included by the current Microsoft template.
