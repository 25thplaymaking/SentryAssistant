using System.Collections.ObjectModel;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Sentry.Contracts;
using SentryAssistant.Models;
using SentryAssistant.Services;
using Windows.Media.Core;
using Windows.Media.Playback;
using Windows.Storage;
using Windows.Storage.Pickers;
using Windows.System;

namespace SentryAssistant;

public sealed partial class MainPage : Page
{
    private readonly SettingsService _settings = new();
    private readonly MicrophoneService _microphone = new();
    private readonly MediaPlayer _player = new();
    private OpenAIService? _openAI;
    private SentryGatewayClient? _gateway;
    private FileSystemWatcher? _watcher;
    private SentryLifecycleState _lifecycleState = SentryLifecycleState.Idle;
    private readonly Dictionary<string, DateTimeOffset> _recentChanges = new(StringComparer.OrdinalIgnoreCase);

    public ObservableCollection<AssistantMessage> Messages { get; } = [];
    public ObservableCollection<CodeActivity> CodeChanges { get; } = [];
    public ObservableCollection<string> ActivityLog { get; } = [];

    public MainPage()
    {
        InitializeComponent();
        Loaded += MainPage_Loaded;
        Unloaded += (_, _) => _watcher?.Dispose();
    }

    private async void MainPage_Loaded(object sender, RoutedEventArgs e)
    {
        await _settings.LoadAsync();
        _openAI = new OpenAIService(_settings);
        ApplySettingsToControls();
        NavigateTo("assistant");
        SetLifecycleState(SentryLifecycleState.Idle);
        Messages.Add(new AssistantMessage(
            MessageAuthor.Sentry,
            _settings.Settings.AssistantName,
            "Online. I can help directly, accept a voice reply, or watch a code workspace for changes.",
            DateTimeOffset.Now));
        ActivityLog.Insert(0, "Sentry Assistant started.");

        _gateway = new SentryGatewayClient(_settings.Settings.GatewayUrl, credentials: _settings);
        SetupGatewayBox.Text = _settings.Settings.GatewayUrl;

        // Resume a stored device credential rather than making someone redeem a
        // fresh enrolment code every launch. The outcome decides where the
        // walkthrough opens, including the case where the credential was revoked.
        var restore = await _gateway.RestoreSessionAsync();
        _setup = restore.ToProgress(_settings.Settings.GatewayUrl);

        // Render before any button is pressed so accessible names and enabled
        // states match the state machine from the outset.
        RenderSetup();

        if (restore.IsAuthenticated)
        {
            ActivityLog.Insert(0, $"{DateTime.Now:t} Signed in with stored credentials.");
        }
        else if (restore.RequiresReEnrolment)
        {
            ActivityLog.Insert(0, $"{DateTime.Now:t} Stored credentials were rejected.");
        }

        await RefreshGatewayStatusAsync();
    }

    /// <summary>
    /// Replaces what were hardcoded status labels. Anything not actually probed
    /// shows UNKNOWN rather than implying a connection that was never checked.
    /// </summary>
    private async Task RefreshGatewayStatusAsync()
    {
        if (_gateway is null) return;

        RefreshStatusButton.IsEnabled = false;
        try
        {
            var status = await _gateway.GetStatusAsync();
            Apply(GatewayStatusText, status.Gateway);
            Apply(RuntimeStatusText, status.Runtime);
            Apply(NodeStatusText, status.Node);

            StatusDetailText.Text = _gateway.IsConfigured
                ? $"{status.Gateway.Detail} · checked {status.CheckedAt:t}"
                : "Set a gateway address in Settings to connect.";
        }
        finally
        {
            RefreshStatusButton.IsEnabled = true;
        }
    }

    private void Apply(TextBlock target, ComponentStatus status)
    {
        target.Text = status.Label;
        target.Foreground = ToneBrush(status.Tone);
        ToolTipService.SetToolTip(target, status.Detail);
        AutomationProperties.SetName(target, $"{status.Name}: {status.Label}. {status.Detail}");
    }

    private Brush ToneBrush(PresenceTone tone)
    {
        var key = tone switch
        {
            PresenceTone.Positive => "SentrySuccessBrush",
            PresenceTone.Pending => "SentryWarningBrush",
            PresenceTone.Attention => "SentryInfoBrush",
            PresenceTone.Critical => "SentryDangerBrush",
            _ => "SentryTextMutedBrush"
        };
        return (Brush)Application.Current.Resources[key];
    }

    private async void RefreshStatus_Click(object sender, RoutedEventArgs e) =>
        await RefreshGatewayStatusAsync();

    // --- Setup walkthrough ---------------------------------------------------

    private SetupProgress _setup = SetupProgress.Initial();

    /// <summary>Pushes the state machine onto the three step cards.</summary>
    private void RenderSetup()
    {
        Render(SetupStepId.GatewayAddress, 1, Step1Marker, Step1State, Step1Detail, Step1Button);
        Render(SetupStepId.EnrolDevice, 2, Step2Marker, Step2State, Step2Detail, Step2Button);
        Render(SetupStepId.VerifyRuntime, 3, Step3Marker, Step3State, Step3Detail, Step3Button);

        SetupComplete.IsOpen = _setup.IsComplete;
        SetupSubtitle.Text = _setup.IsComplete
            ? "This desktop is connected and enrolled."
            : $"Step {_setup.CompletedCount + 1} of {_setup.Steps.Count}. "
              + _setup.CurrentStep?.Description;

        void Render(
            SetupStepId id, int ordinal,
            TextBlock marker, TextBlock state, TextBlock detail, Button action)
        {
            var step = _setup[id];
            marker.Text = step.Marker(ordinal);
            state.Text = step.State.ToString().ToUpperInvariant();
            state.Foreground = ToneBrush(step.Tone);
            detail.Text = step.Detail;
            detail.Foreground = ToneBrush(
                step.State is SetupStepState.Failed ? PresenceTone.Critical : PresenceTone.Neutral);

            // A blocked or in-flight step is disabled rather than hidden, so the
            // shape of the walkthrough stays visible throughout.
            action.IsEnabled = step.CanAttempt;
            AutomationProperties.SetName(
                action, $"{step.Title}. {step.State}. {step.Detail}");
        }
    }

    private void SetSetupStep(SetupStepId id, SetupStepState state, string detail = "")
    {
        _setup = _setup.With(id, state, detail);
        RenderSetup();
    }

    private async void SetupStep1_Click(object sender, RoutedEventArgs e)
    {
        var address = SetupGatewayBox.Text.Trim();
        if (string.IsNullOrWhiteSpace(address))
        {
            SetSetupStep(SetupStepId.GatewayAddress, SetupStepState.Failed, "Enter a gateway address.");
            return;
        }

        SetSetupStep(SetupStepId.GatewayAddress, SetupStepState.Working, "Contacting gateway...");

        _gateway?.Dispose();
        _gateway = new SentryGatewayClient(address, credentials: _settings);
        var status = await _gateway.GetStatusAsync();

        if (status.Gateway.IsHealthy)
        {
            _settings.Settings.GatewayUrl = address;
            await _settings.SaveAsync();
            SetSetupStep(SetupStepId.GatewayAddress, SetupStepState.Done, $"Connected to {address}.");
            ActivityLog.Insert(0, $"{DateTime.Now:t} Gateway address saved.");

            // A gateway that has just come back — a tunnel restarted, say — can
            // often be resumed with the credential already on disk, which turns a
            // three-step recovery back into one.
            var restore = await _gateway.RestoreSessionAsync();
            if (restore.IsAuthenticated || restore.RequiresReEnrolment)
            {
                _setup = restore.ToProgress(address);
                RenderSetup();
            }
        }
        else
        {
            SetSetupStep(SetupStepId.GatewayAddress, SetupStepState.Failed, status.Gateway.Detail);
        }

        await RefreshGatewayStatusAsync();
    }

    private async void SetupStep2_Click(object sender, RoutedEventArgs e)
    {
        if (_gateway is null) return;

        var code = SetupCodeBox.Text.Trim();
        if (string.IsNullOrWhiteSpace(code))
        {
            SetSetupStep(SetupStepId.EnrolDevice, SetupStepState.Failed, "Enter the enrolment code.");
            return;
        }

        SetSetupStep(SetupStepId.EnrolDevice, SetupStepState.Working, "Redeeming code...");
        var (success, detail) = await _gateway.EnrolAsync(code, Environment.MachineName);

        SetSetupStep(
            SetupStepId.EnrolDevice,
            success ? SetupStepState.Done : SetupStepState.Failed,
            detail);

        if (success)
        {
            // The code is single use; leaving it on screen only invites a retry
            // that is guaranteed to fail.
            SetupCodeBox.Text = string.Empty;
            ActivityLog.Insert(0, $"{DateTime.Now:t} Device enrolled with the gateway.");
        }
    }

    private async void SetupStep3_Click(object sender, RoutedEventArgs e)
    {
        if (_gateway is null) return;

        SetSetupStep(SetupStepId.VerifyRuntime, SetupStepState.Working, "Checking the runtime...");
        var status = await _gateway.GetStatusAsync();

        if (!status.Runtime.IsHealthy)
        {
            SetSetupStep(SetupStepId.VerifyRuntime, SetupStepState.Failed, status.Runtime.Detail);
            return;
        }

        // Healthy is not the same as useful: report the missing-model case here
        // rather than declaring success and letting the first question fail.
        var (_, snapshot) = await _gateway.GetHermesAdminAsync();
        if (snapshot is { CanAnswer: false })
        {
            SetSetupStep(
                SetupStepId.VerifyRuntime, SetupStepState.Failed,
                "Runtime is reachable but has no inference provider, so it cannot answer yet.");
            return;
        }

        SetSetupStep(SetupStepId.VerifyRuntime, SetupStepState.Done, status.Runtime.Detail);
        await RefreshGatewayStatusAsync();
    }

    // --- Hermes control centre -----------------------------------------------

    public ObservableCollection<FindingRow> HermesFindingRows { get; } = [];
    public ObservableCollection<CapabilityRow> HermesCapabilityRows { get; } = [];

    private async Task RefreshHermesAsync()
    {
        HermesRefreshButton.IsEnabled = false;
        try
        {
            var (access, snapshot) = _gateway is null
                ? (SentryGatewayClient.AdminAccess.NotEnrolled, null)
                : await _gateway.GetHermesAdminAsync();

            if (snapshot is null)
            {
                // Name the actual cause. Reporting a timeout as a permissions
                // problem sends someone to fix the wrong thing.
                HermesAccessInfo.IsOpen = true;
                HermesAccessInfo.Title = access switch
                {
                    SentryGatewayClient.AdminAccess.NotAdministrator => "Administrator access required",
                    SentryGatewayClient.AdminAccess.NotEnrolled => "Setup incomplete",
                    _ => "Gateway did not answer"
                };
                HermesAccessInfo.Message = access switch
                {
                    SentryGatewayClient.AdminAccess.NotAdministrator =>
                        "This device is enrolled, but its owner is not an administrator.",
                    SentryGatewayClient.AdminAccess.NotEnrolled =>
                        "Finish the setup walkthrough to view runtime detail.",
                    _ => "The admin endpoint did not respond. The runtime probe can be slow if the runtime is unhealthy."
                };
                HermesAccessInfo.Severity = access is SentryGatewayClient.AdminAccess.Unreachable
                    ? InfoBarSeverity.Error
                    : InfoBarSeverity.Informational;

                HermesHeadline.Text = "UNAVAILABLE";
                HermesHeadline.Foreground = ToneBrush(PresenceTone.Neutral);
                HermesIdentity.Text = "Runtime detail not available";
                HermesSubDetail.Text = HermesAccessInfo.Message;
                HermesPresence.State = SentryLifecycleState.Offline;
                HermesFindingRows.Clear();
                HermesCapabilityRows.Clear();
                return;
            }

            HermesAccessInfo.IsOpen = false;
            HermesHeadline.Text = snapshot.HeadlineLabel;
            HermesHeadline.Foreground = ToneBrush(snapshot.HeadlineTone);
            HermesIdentity.Text = $"{snapshot.RuntimeName} {snapshot.PinnedVersion}";
            HermesSubDetail.Text =
                $"{snapshot.ProfilesRegistered} profile(s) registered · checked {DateTime.Now:t}";

            HermesPresence.State = snapshot.FullyOperational
                ? SentryLifecycleState.Idle
                : SentryLifecycleState.Failed;

            HermesFindingRows.Clear();
            foreach (var finding in snapshot.Findings)
            {
                HermesFindingRows.Add(new FindingRow(finding));
            }

            HermesCapabilityRows.Clear();
            var caps = snapshot.Capabilities;
            HermesCapabilityRows.Add(new CapabilityRow("Sessions", caps.Sessions));
            HermesCapabilityRows.Add(new CapabilityRow("Session search", caps.SessionSearch));
            HermesCapabilityRows.Add(new CapabilityRow("Work board", caps.WorkBoard));
            HermesCapabilityRows.Add(new CapabilityRow("Cancellation", caps.Cancellation));
            HermesCapabilityRows.Add(new CapabilityRow("Delegation", caps.Delegation));
        }
        finally
        {
            HermesRefreshButton.IsEnabled = true;
        }
    }

    private async void HermesRefresh_Click(object sender, RoutedEventArgs e) =>
        await RefreshHermesAsync();

    private void ApplySettingsToControls()
    {
        var value = _settings.Settings;
        AssistantNameBox.Text = value.AssistantName;
        AssistantModelBox.Text = value.AssistantModel;
        SummaryModelBox.Text = value.SummaryModel;
        SummaryWordLimitBox.Value = value.SummaryWordLimit;
        BriefSummaryToggle.IsOn = value.BriefResolutionSummaries;
        SpeechModelBox.Text = value.SpeechModel;
        TranscriptionModelBox.Text = value.TranscriptionModel;
        VoiceStyleBox.Text = value.VoiceStyle;
        SettingsSpeakToggle.IsOn = value.SpeakResolutions;
        SpeakToggle.IsOn = value.SpeakResolutions;
        InspectorSpeakToggle.IsOn = value.SpeakResolutions;
        SettingsWatchPathBox.Text = value.WatchedFolder;
        WatchPathBox.Text = value.WatchedFolder;
        NotifyChangesToggle.IsOn = value.NotifyOnCodeChanges;
        SpeechVoiceBox.SelectedItem = SpeechVoiceBox.Items.OfType<ComboBoxItem>().FirstOrDefault(item => (string)item.Content == value.SpeechVoice);
        ApiKeyStatusText.Text = string.IsNullOrWhiteSpace(_settings.GetApiKey()) ? "No API key is configured." : "API key is protected for this Windows account.";
    }

    private void Navigate_Click(object sender, RoutedEventArgs e)
    {
        NavigateTo((sender as ToggleButton)?.Tag as string ?? "assistant");
    }

    private void NavigateTo(string tag)
    {
        AssistantPage.Visibility = tag == "assistant" ? Visibility.Visible : Visibility.Collapsed;
        WatcherPage.Visibility = tag == "watcher" ? Visibility.Visible : Visibility.Collapsed;
        ActivityPage.Visibility = tag == "activity" ? Visibility.Visible : Visibility.Collapsed;
        SettingsPage.Visibility = tag == "settings" ? Visibility.Visible : Visibility.Collapsed;
        SetupPage.Visibility = tag == "setup" ? Visibility.Visible : Visibility.Collapsed;
        HermesPage.Visibility = tag == "hermes" ? Visibility.Visible : Visibility.Collapsed;

        AssistantNavButton.IsChecked = tag == "assistant";
        WatcherNavButton.IsChecked = tag == "watcher";
        ActivityNavButton.IsChecked = tag == "activity";
        SettingsNavButton.IsChecked = tag == "settings";
        SetupNavButton.IsChecked = tag == "setup";
        HermesNavButton.IsChecked = tag == "hermes";

        CurrentPageTitle.Text = tag switch
        {
            "watcher" => "Code watcher",
            "activity" => "Activity",
            "settings" => "Settings",
            "setup" => "Setup",
            "hermes" => "Hermes control centre",
            _ => "Assistant"
        };

        // Fetch on entry so the view is never showing a stale picture of a
        // runtime that may have changed since the app started.
        if (tag == "hermes") _ = RefreshHermesAsync();
    }

    private void OpenSettings_Click(object sender, RoutedEventArgs e) => NavigateTo("settings");

    private void InspectorSpeakToggle_Toggled(object sender, RoutedEventArgs e)
    {
        SpeakToggle.IsOn = InspectorSpeakToggle.IsOn;
        SettingsSpeakToggle.IsOn = InspectorSpeakToggle.IsOn;
    }

    /// <summary>
    /// Single entry point for lifecycle changes. Labels and detail text come from
    /// the tested presence policy; callers may override detail with something more
    /// specific but cannot invent a new state name.
    /// </summary>
    private void SetLifecycleState(SentryLifecycleState state, string? detail = null)
    {
        _lifecycleState = state;
        Presence.State = state;

        var descriptor = SentryPresence.Describe(state);
        AssistantStateText.Text = descriptor.Label;
        AssistantStateDetail.Text = detail ?? descriptor.Detail;
    }

    /// <summary>
    /// Resolution-only speech. Both the user's mute toggle and the lifecycle policy
    /// must allow it, so no command, tool, or progress event can ever reach TTS.
    /// </summary>
    private bool MaySpeakNow() =>
        SpeakToggle.IsOn && SentryPresence.Describe(_lifecycleState).MaySpeak;

    private void NewWorkOrder_Click(object sender, RoutedEventArgs e)
    {
        Messages.Clear();
        NavigateTo("assistant");
        SetLifecycleState(SentryLifecycleState.Idle, "New session — awaiting your first message");
        Messages.Add(new AssistantMessage(
            MessageAuthor.System,
            "Sentry",
            "New local session. Durable team work orders become available once a gateway is connected.",
            DateTimeOffset.Now));
        AssistantStatus.Severity = InfoBarSeverity.Informational;
        AssistantStatus.Title = "New session";
        AssistantStatus.Message = "Ask Sentry a question or use push-to-talk.";
        ActivityLog.Insert(0, $"{DateTime.Now:t} Started a new local session.");
        PromptBox.Focus(FocusState.Programmatic);
    }

    private async void SendButton_Click(object sender, RoutedEventArgs e) => await SendPromptAsync();

    private async void PromptBox_KeyDown(object sender, KeyRoutedEventArgs e)
    {
        if (e.Key == VirtualKey.Enter && !string.IsNullOrWhiteSpace(PromptBox.Text))
        {
            e.Handled = true;
            await SendPromptAsync();
        }
    }

    private async Task SendPromptAsync()
    {
        if (_openAI is null || string.IsNullOrWhiteSpace(PromptBox.Text)) return;
        if (string.IsNullOrWhiteSpace(_settings.GetApiKey()))
        {
            AssistantStatus.Severity = InfoBarSeverity.Warning;
            AssistantStatus.Title = "OpenAI key needed";
            AssistantStatus.Message = "Open Settings to add or import your API key.";
            return;
        }

        var prompt = PromptBox.Text.Trim();
        PromptBox.Text = string.Empty;
        Messages.Add(new AssistantMessage(MessageAuthor.User, "Bryce", prompt, DateTimeOffset.Now));
        AssistantStatus.Severity = InfoBarSeverity.Informational;
        AssistantStatus.Title = "Working";
        AssistantStatus.Message = "Sentry is preparing a response.";
        SetLifecycleState(SentryLifecycleState.Running);
        SendButton.IsEnabled = false;
        try
        {
            var response = await _openAI.AskAsync(prompt, Messages.ToList());
            Messages.Add(new AssistantMessage(
                MessageAuthor.Sentry, _settings.Settings.AssistantName, response, DateTimeOffset.Now));
            ConversationList.ScrollIntoView(Messages[^1]);
            AssistantStatus.Severity = InfoBarSeverity.Success;
            AssistantStatus.Title = "Resolved";
            AssistantStatus.Message = "Response ready.";
            SetLifecycleState(SentryLifecycleState.Resolved);
            ActivityLog.Insert(0, $"{DateTime.Now:t} Assistant response resolved.");

            // Speech is gated on the resolved lifecycle state, not merely on reaching this line.
            if (MaySpeakNow())
            {
                var spoken = await _openAI.SummarizeAsync(response);
                await PlaySpeechAsync(spoken);
            }
        }
        catch (Exception exception)
        {
            AssistantStatus.Severity = InfoBarSeverity.Error;
            AssistantStatus.Title = "Assistant unavailable";
            AssistantStatus.Message = exception.Message;
            SetLifecycleState(SentryLifecycleState.Failed, "Assistant connection unavailable");
        }
        finally
        {
            SendButton.IsEnabled = true;
            PromptBox.Focus(FocusState.Programmatic);
        }
    }

    private async Task PlaySpeechAsync(string text)
    {
        if (_openAI is null) return;
        var path = await _openAI.CreateSpeechAsync(text);
        var file = await StorageFile.GetFileFromPathAsync(path);
        _player.Source = MediaSource.CreateFromStorageFile(file);
        _player.Play();
    }

    private async void RecordButton_Click(object sender, RoutedEventArgs e)
    {
        if (_openAI is null) return;
        try
        {
            if (!_microphone.IsRecording)
            {
                _player.Pause();
                _microphone.Start();
                SetRecordButtonState(recording: true);
                AssistantStatus.Severity = InfoBarSeverity.Warning;
                AssistantStatus.Title = "Listening";
                AssistantStatus.Message = "Speak naturally, then press Stop and transcribe.";
                SetLifecycleState(SentryLifecycleState.NeedsInput, "Push-to-talk is active");
                return;
            }

            RecordButton.IsEnabled = false;
            var path = _microphone.Stop();
            AssistantStatus.Title = "Transcribing";
            AssistantStatus.Message = "Turning your voice into a reviewable message.";
            var text = await _openAI.TranscribeAsync(path);
            PromptBox.Text = text;
            PromptBox.Focus(FocusState.Programmatic);
            PromptBox.SelectionStart = PromptBox.Text.Length;
            AssistantStatus.Severity = InfoBarSeverity.Informational;
            AssistantStatus.Title = "Voice reply ready";
            AssistantStatus.Message = "Review the message, then press Send.";
            SetLifecycleState(SentryLifecycleState.NeedsInput, "Review transcription before sending");
        }
        catch (Exception exception)
        {
            AssistantStatus.Severity = InfoBarSeverity.Error;
            AssistantStatus.Title = "Microphone unavailable";
            AssistantStatus.Message = exception.Message;
            SetLifecycleState(SentryLifecycleState.Failed, "Microphone unavailable");
        }
        finally
        {
            if (!_microphone.IsRecording) SetRecordButtonState(recording: false);
            RecordButton.IsEnabled = true;
        }
    }

    /// <summary>
    /// Push-to-talk is a single toggle, so the glyph, tooltip, and accessible name
    /// must all change together — a colour change alone would not be announced.
    /// </summary>
    private void SetRecordButtonState(bool recording)
    {
        //  stop,  microphone (Segoe Fluent Icons).
        // 0xE71A stop, 0xE720 microphone (Segoe Fluent Icons).
        RecordGlyph.Glyph = ((char)(recording ? 0xE71A : 0xE720)).ToString();
        var name = recording ? "Stop recording and transcribe" : "Push to talk";
        AutomationProperties.SetName(RecordButton, name);
        ToolTipService.SetToolTip(RecordButton, recording
            ? "Stop recording and transcribe for review."
            : "Push to talk. Your words are transcribed for review before sending.");
    }

    private async void BrowseFolder_Click(object sender, RoutedEventArgs e)
    {
        var picker = new FolderPicker();
        picker.FileTypeFilter.Add("*");
        WinRT.Interop.InitializeWithWindow.Initialize(picker, WinRT.Interop.WindowNative.GetWindowHandle(MainWindow.Instance));
        var folder = await picker.PickSingleFolderAsync();
        if (folder is not null) WatchPathBox.Text = folder.Path;
    }

    private void WatchButton_Click(object sender, RoutedEventArgs e)
    {
        if (_watcher is not null)
        {
            _watcher.Dispose();
            _watcher = null;
            WatchButton.Label = "Start watching";
            WatcherStatus.Title = "Paused";
            WatcherStatus.Message = "Code watching is paused.";
            return;
        }
        if (!Directory.Exists(WatchPathBox.Text))
        {
            WatcherStatus.Severity = InfoBarSeverity.Error;
            WatcherStatus.Title = "Folder not found";
            WatcherStatus.Message = "Choose an existing code folder.";
            return;
        }

        _watcher = new FileSystemWatcher(WatchPathBox.Text)
        {
            IncludeSubdirectories = true,
            NotifyFilter = NotifyFilters.FileName | NotifyFilters.LastWrite | NotifyFilters.DirectoryName,
            EnableRaisingEvents = true
        };
        _watcher.Changed += (_, args) => QueueCodeChange("Changed", args.FullPath);
        _watcher.Created += (_, args) => QueueCodeChange("Created", args.FullPath);
        _watcher.Deleted += (_, args) => QueueCodeChange("Deleted", args.FullPath);
        _watcher.Renamed += (_, args) => QueueCodeChange("Renamed", args.FullPath);
        WatchButton.Label = "Stop watching";
        WatcherStatus.Severity = InfoBarSeverity.Success;
        WatcherStatus.Title = "Watching";
        WatcherStatus.Message = WatchPathBox.Text;
        ActivityLog.Insert(0, $"{DateTime.Now:t} Started watching {WatchPathBox.Text}");
    }

    private void QueueCodeChange(string kind, string path)
    {
        if (path.Contains("\\.git\\", StringComparison.OrdinalIgnoreCase) ||
            path.Contains("\\bin\\", StringComparison.OrdinalIgnoreCase) ||
            path.Contains("\\obj\\", StringComparison.OrdinalIgnoreCase) ||
            path.Contains("\\node_modules\\", StringComparison.OrdinalIgnoreCase)) return;
        var now = DateTimeOffset.Now;
        lock (_recentChanges)
        {
            if (_recentChanges.TryGetValue(path, out var seen) && now - seen < TimeSpan.FromMilliseconds(800)) return;
            _recentChanges[path] = now;
        }
        DispatcherQueue.TryEnqueue(() =>
        {
            CodeChanges.Insert(0, new CodeActivity(kind, path, now));
            while (CodeChanges.Count > 300) CodeChanges.RemoveAt(CodeChanges.Count - 1);
            ActivityLog.Insert(0, $"{now.LocalDateTime:t} {kind}: {Path.GetFileName(path)}");
        });
    }

    private async void SaveSettings_Click(object sender, RoutedEventArgs e)
    {
        var value = _settings.Settings;
        value.AssistantName = AssistantNameBox.Text.Trim();
        value.AssistantModel = AssistantModelBox.Text.Trim();
        value.SummaryModel = SummaryModelBox.Text.Trim();
        value.SummaryWordLimit = (int)SummaryWordLimitBox.Value;
        value.BriefResolutionSummaries = BriefSummaryToggle.IsOn;
        value.SpeechModel = SpeechModelBox.Text.Trim();
        value.TranscriptionModel = TranscriptionModelBox.Text.Trim();
        value.VoiceStyle = VoiceStyleBox.Text.Trim();
        value.SpeechVoice = (SpeechVoiceBox.SelectedItem as ComboBoxItem)?.Content as string ?? "coral";
        value.SpeakResolutions = SettingsSpeakToggle.IsOn;
        value.WatchedFolder = SettingsWatchPathBox.Text.Trim();
        value.NotifyOnCodeChanges = NotifyChangesToggle.IsOn;
        if (!string.IsNullOrWhiteSpace(ApiKeyBox.Password)) _settings.SetApiKey(ApiKeyBox.Password);
        await _settings.SaveAsync();
        SpeakToggle.IsOn = value.SpeakResolutions;
        InspectorSpeakToggle.IsOn = value.SpeakResolutions;
        WatchPathBox.Text = value.WatchedFolder;
        ApiKeyBox.Password = string.Empty;
        ApiKeyStatusText.Text = string.IsNullOrWhiteSpace(_settings.GetApiKey()) ? "No API key is configured." : "API key is protected for this Windows account.";
        SettingsStatus.Severity = InfoBarSeverity.Success;
        SettingsStatus.Title = "Saved";
        SettingsStatus.Message = "All Sentry settings were saved.";
        SettingsStatus.IsOpen = true;
    }

    private async void TestConnection_Click(object sender, RoutedEventArgs e)
    {
        if (_openAI is null) return;
        try
        {
            var result = await _openAI.AskAsync("Reply with exactly: Sentry connection ready.", []);
            SettingsStatus.Severity = InfoBarSeverity.Success;
            SettingsStatus.Title = "Connected";
            SettingsStatus.Message = result;
        }
        catch (Exception exception)
        {
            SettingsStatus.Severity = InfoBarSeverity.Error;
            SettingsStatus.Title = "Connection failed";
            SettingsStatus.Message = exception.Message;
        }
        SettingsStatus.IsOpen = true;
    }
}
