using System.Collections.ObjectModel;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
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
    private FileSystemWatcher? _watcher;
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
        Shell.SelectedItem = Shell.MenuItems[0];
        Messages.Add(new AssistantMessage("Sentry", "Online. I can help directly, accept a voice reply, or watch a code workspace for changes.", DateTimeOffset.Now));
        ActivityLog.Insert(0, "Sentry Assistant started.");
    }

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
        SettingsWatchPathBox.Text = value.WatchedFolder;
        WatchPathBox.Text = value.WatchedFolder;
        NotifyChangesToggle.IsOn = value.NotifyOnCodeChanges;
        SpeechVoiceBox.SelectedItem = SpeechVoiceBox.Items.OfType<ComboBoxItem>().FirstOrDefault(item => (string)item.Content == value.SpeechVoice);
        ApiKeyStatusText.Text = string.IsNullOrWhiteSpace(_settings.GetApiKey()) ? "No API key is configured." : "API key is protected for this Windows account.";
    }

    private void Shell_SelectionChanged(NavigationView sender, NavigationViewSelectionChangedEventArgs args)
    {
        var tag = (args.SelectedItem as NavigationViewItem)?.Tag as string ?? "assistant";
        AssistantPage.Visibility = tag == "assistant" ? Visibility.Visible : Visibility.Collapsed;
        WatcherPage.Visibility = tag == "watcher" ? Visibility.Visible : Visibility.Collapsed;
        ActivityPage.Visibility = tag == "activity" ? Visibility.Visible : Visibility.Collapsed;
        SettingsPage.Visibility = tag == "settings" ? Visibility.Visible : Visibility.Collapsed;
    }

    private void OpenSettings_Click(object sender, RoutedEventArgs e) => Shell.SelectedItem = Shell.FooterMenuItems[0];

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
        Messages.Add(new AssistantMessage("Bryce", prompt, DateTimeOffset.Now));
        AssistantStatus.Title = "Working";
        AssistantStatus.Message = "Sentry is preparing a response.";
        SendButton.IsEnabled = false;
        try
        {
            var response = await _openAI.AskAsync(prompt, Messages.ToList());
            Messages.Add(new AssistantMessage(_settings.Settings.AssistantName, response, DateTimeOffset.Now));
            ConversationList.ScrollIntoView(Messages[^1]);
            AssistantStatus.Severity = InfoBarSeverity.Success;
            AssistantStatus.Title = "Resolved";
            AssistantStatus.Message = "Response ready.";
            ActivityLog.Insert(0, $"{DateTime.Now:t} Assistant response resolved.");
            if (SpeakToggle.IsOn)
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
                RecordButton.Label = "Stop and transcribe";
                AssistantStatus.Severity = InfoBarSeverity.Warning;
                AssistantStatus.Title = "Listening";
                AssistantStatus.Message = "Speak naturally, then press Stop and transcribe.";
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
        }
        catch (Exception exception)
        {
            AssistantStatus.Severity = InfoBarSeverity.Error;
            AssistantStatus.Title = "Microphone unavailable";
            AssistantStatus.Message = exception.Message;
        }
        finally
        {
            if (!_microphone.IsRecording) RecordButton.Label = "Speak";
            RecordButton.IsEnabled = true;
        }
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
